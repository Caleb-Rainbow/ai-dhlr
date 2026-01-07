"""
FastAPI主服务
"""
from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import HTMLResponse, FileResponse
from contextlib import asynccontextmanager
import asyncio
from pathlib import Path

from .routes import cameras, zones, status, control, settings
from .websocket import ws_manager, broadcast_status_update
from ..zone.state_machine import zone_manager
from ..utils.logger import get_logger

logger = get_logger()

# 状态推送任务
_status_broadcast_task = None

# 静态文件目录
STATIC_DIR = Path(__file__).parent.parent / "static"
SNAPSHOT_DIR = Path(__file__).parent.parent.parent / "snapshots"


async def status_broadcast_loop():
    """定期广播状态更新"""
    while True:
        try:
            statuses = zone_manager.get_all_status()
            if ws_manager.active_connections:
                await broadcast_status_update(statuses)
            await asyncio.sleep(1.0)  # 每秒更新一次
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
    _status_broadcast_task = asyncio.create_task(status_broadcast_loop())
    
    # 启动网络监测
    try:
        from ..utils.network_monitor import network_monitor
        await network_monitor.start_async(interval=10.0)
    except Exception as e:
        logger.warning(f"网络监测启动失败: {e}")
    
    # 启动远程 WebSocket 客户端（如果配置了）
    try:
        from ..utils.config import config_manager
        if config_manager.config.remote.enabled:
            from .websocket_client import remote_ws_client
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



def create_app() -> FastAPI:
    """创建FastAPI应用"""
    app = FastAPI(
        title="动火离人安全监测系统",
        description="厨房安全监测系统API",
        version="0.1.0",
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
    
    # 注册API路由
    app.include_router(cameras.router)
    app.include_router(zones.router)
    app.include_router(status.router)
    app.include_router(control.router)
    app.include_router(settings.router)
    
    # WebSocket端点
    @app.websocket("/ws/status")
    async def websocket_endpoint(websocket: WebSocket):
        await ws_manager.connect(websocket)
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
    
    # 首页 - 返回Web管理界面
    @app.get("/", response_class=HTMLResponse)
    async def index():
        index_file = STATIC_DIR / "index.html"
        if index_file.exists():
            return HTMLResponse(content=index_file.read_text(encoding='utf-8'))
        return HTMLResponse(content="<h1>Web界面未找到</h1>", status_code=404)
    
    # 截图访问
    @app.get("/snapshot/{filename}")
    async def get_snapshot(filename: str):
        filepath = SNAPSHOT_DIR / filename
        if filepath.exists() and filepath.suffix.lower() in ['.jpg', '.jpeg', '.png']:
            return FileResponse(filepath, media_type="image/jpeg")
        return Response(content="Not Found", status_code=404)
    
    # 健康检查
    @app.get("/health")
    async def health():
        return {"status": "healthy"}
    
    # API信息
    @app.get("/api")
    async def api_info():
        return {
            "name": "动火离人安全监测系统",
            "version": "0.1.0",
            "status": "running",
            "docs": "/docs"
        }
    
    # 挂载静态文件目录（CSS、JS）
    if STATIC_DIR.exists():
        app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")
    
    return app

