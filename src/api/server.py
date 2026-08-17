"""
FastAPI主服务
"""
from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from src.version import __version__
from fastapi.responses import FileResponse
from contextlib import asynccontextmanager
import asyncio
from pathlib import Path

from .websocket import ws_manager, broadcast_status_update, message_dispatcher
from ..zone.state_machine import zone_manager
from ..utils.logger import get_logger

# 前端静态资源目录
FRONTEND_DIST_DIR = Path(__file__).parent.parent.parent / "web" / "fire-monitor-ui" / "dist"

logger = get_logger()

# 状态推送任务
_status_broadcast_task = None

# 快照目录
SNAPSHOT_DIR = Path(__file__).parent.parent.parent / "snapshots"


def _status_hash(statuses: list) -> str:
    """生成状态哈希，用于检测状态变化"""
    if not statuses:
        return ""
    # 使用关键字段生成哈希，忽略频繁变化的字段（如 last_seen_time）
    import hashlib
    import json
    key_fields = []
    for s in statuses:
        key_fields.append({
            "id": s.get("id"),
            "state": s.get("state"),
            "is_fire_on": s.get("is_fire_on"),
            "has_person": s.get("has_person"),  # 是否有人（前端人形图标状态）
            "current_value": s.get("current_value"),  # 电流值（前端显示电流）
            "no_person_duration": int(s.get("no_person_duration", 0)),  # 整数比较，避免秒级变化
        })
    return hashlib.md5(json.dumps(key_fields, sort_keys=True).encode()).hexdigest()


async def status_broadcast_loop():
    """定期广播状态更新

    优化：仅在状态变化时广播，减少不必要的网络传输。
    """
    last_status_hash = None
    consecutive_unchanged = 0

    while True:
        try:
            statuses = zone_manager.get_all_status()
            current_hash = _status_hash(statuses)

            if ws_manager.active_connections:
                # 状态变化时广播，或每10秒强制广播一次（保持心跳）
                if current_hash != last_status_hash:
                    await broadcast_status_update(statuses)
                    last_status_hash = current_hash
                    consecutive_unchanged = 0
                    logger.debug("状态变化，广播状态更新")
                else:
                    consecutive_unchanged += 1
                    # 每10秒强制广播一次，作为心跳检测
                    if consecutive_unchanged >= 10:
                        await broadcast_status_update(statuses)
                        consecutive_unchanged = 0
                        logger.debug("心跳广播状态更新")

            await asyncio.sleep(1.0)  # 每秒检查一次
        except asyncio.CancelledError:
            break
        except Exception as e:
            logger.error(f"状态广播错误: {e}")
            await asyncio.sleep(1.0)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """应用生命周期管理"""
    global _status_broadcast_task
    
    # 启动时
    logger.info("FastAPI服务启动")
    
    # 保存主事件循环引用（用于从非异步上下文发送 WebSocket 消息）
    try:
        main_loop = asyncio.get_running_loop()
        message_dispatcher.set_main_loop(main_loop)
    except RuntimeError:
        logger.warning("无法获取主事件循环")
    
    _status_broadcast_task = asyncio.create_task(status_broadcast_loop())
    
    # 启动网络监测
    try:
        from ..utils.network_monitor import network_monitor
        await network_monitor.start_async(interval=10.0)
    except Exception as e:
        logger.warning(f"网络监测启动失败: {e}")

    # 启动局域网设备发现服务（UDP 广播，供 APP/PC/脚本扫描发现）
    try:
        from ..discovery import discovery_service
        await discovery_service.start()
    except Exception as e:
        logger.warning(f"设备发现服务启动失败: {e}")

    # 启动远程 WebSocket 客户端（如果配置了）
    try:
        from ..utils.config import config_manager
        from .websocket_client import remote_ws_client
        from .ws_handler import ws_handler

        # 注册远程消息处理器。必须无条件注册：配置页热启用远程模式时
        # _update_remote_config 只重启连接、不会注册处理器，若此处随
        # enabled 跳过，设备能推 status_update 但所有请求被静默丢弃，
        # 断电重启才恢复
        async def handle_remote_request(message: dict):
            """处理来自远程服务器的请求"""
            if message.get('type') == 'request':
                response = await ws_handler.handle_request(message)
                await remote_ws_client.send(response)

        await remote_ws_client.add_message_handler(handle_remote_request)

        if config_manager.config.remote.enabled:
            asyncio.create_task(remote_ws_client.start())
            logger.info("远程 WebSocket 客户端已启动")
    except Exception as e:
        logger.warning(f"远程 WebSocket 客户端启动失败: {e}")
    
    # 确保目录存在
    SNAPSHOT_DIR.mkdir(parents=True, exist_ok=True)
    
    yield
    
    # 关闭时
    logger.info("FastAPI服务关闭")
    
    # 停止状态广播
    if _status_broadcast_task:
        _status_broadcast_task.cancel()
        try:
            await _status_broadcast_task
        except asyncio.CancelledError:
            pass
    
    # 停止网络监测
    try:
        from ..utils.network_monitor import network_monitor
        await network_monitor.stop()
    except Exception:
        pass
    
    # 停止远程 WebSocket 客户端
    try:
        from .websocket_client import remote_ws_client
        await remote_ws_client.stop()
    except Exception:
        pass

    # 停止设备发现服务
    try:
        from ..discovery import discovery_service
        await discovery_service.stop()
    except Exception:
        pass



def create_app() -> FastAPI:
    """创建FastAPI应用"""
    app = FastAPI(
        title="动火离人安全监测系统",
        description="厨房安全监测系统API",
        version=__version__,
        lifespan=lifespan
    )
    
    # CORS配置
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # 内部配网接口（仅本机回环；供 ai-dhlr-ble.service 下发 remote/system 配置）
    from .internal_provisioning import router as internal_router
    app.include_router(internal_router)

    # 设备身份识别（返回与 UDP 广播一致的 JSON）
    # 供 Web 浏览器等无法收 UDP 广播的客户端做网段 HTTP 探测发现设备
    from ..discovery import discovery_service

    @app.get("/api/device/identify")
    @app.get("/.well-known/dhlr")
    async def device_identify():
        """设备身份识别（局域网发现 - HTTP 路径）"""
        return discovery_service.get_announcement()

    # WebSocket端点
    @app.websocket("/ws/status")
    async def websocket_endpoint(websocket: WebSocket):
        if not await ws_manager.connect(websocket):
            # 连接被拒绝（达到连接数限制）
            return
        try:
            while True:
                # 接收客户端消息
                data = await websocket.receive_text()
                logger.debug(f"收到WebSocket消息: {data}")

                # 解析并处理消息
                try:
                    import json
                    message = json.loads(data)
                    await ws_manager.handle_message(websocket, message)
                except json.JSONDecodeError:
                    logger.warning(f"无效的JSON消息: {data}")
                except Exception as e:
                    logger.error(f"处理消息失败: {e}")
        except WebSocketDisconnect:
            await ws_manager.disconnect(websocket)
        except Exception as e:
            logger.error(f"WebSocket错误: {e}")
            await ws_manager.disconnect(websocket)

    # =========================================================================
    # 前端静态资源托管
    # =========================================================================
    if FRONTEND_DIST_DIR.exists():
        # 挂载静态资源目录 (JS, CSS, 图片等)
        assets_dir = FRONTEND_DIST_DIR / "assets"
        if assets_dir.exists():
            app.mount("/assets", StaticFiles(directory=str(assets_dir)), name="assets")
        
        # SPA 路由回退: 所有非API/WS请求都返回 index.html
        @app.get("/{full_path:path}")
        async def serve_spa(request: Request, full_path: str):
            """单页应用路由回退"""
            # 如果请求的是具体文件且存在，直接返回
            file_path = FRONTEND_DIST_DIR / full_path
            if file_path.is_file():
                return FileResponse(file_path)
            
            # 否则返回 index.html (SPA 路由)
            index_path = FRONTEND_DIST_DIR / "index.html"
            if index_path.exists():
                return FileResponse(index_path)
            
            # 前端未构建时的友好提示
            return {"error": "前端资源未找到，请先运行 npm run build"}
        
        logger.info(f"前端静态资源已托管: {FRONTEND_DIST_DIR}")
    else:
        logger.warning(f"前端构建目录不存在: {FRONTEND_DIST_DIR}，跳过静态资源托管")
    
    return app

