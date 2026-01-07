"""
WebSocket 请求处理器
处理前端通过 WebSocket 发送的所有请求，实现请求-响应模式
"""
import time
import uuid
import threading
from typing import Dict, Any, Optional, Callable, Awaitable
from dataclasses import dataclass

from ..utils.logger import get_logger
from ..utils.config import config_manager, ZoneConfig, CameraConfig

logger = get_logger()


@dataclass
class WSRequest:
    """WebSocket 请求"""
    msg_id: str
    action: str
    params: Dict[str, Any]


@dataclass
class WSResponse:
    """WebSocket 响应"""
    msg_id: str
    success: bool
    data: Any = None
    error: Optional[str] = None
    
    def to_dict(self) -> dict:
        result = {
            "type": "response",
            "msg_id": self.msg_id,
            "success": self.success
        }
        if self.data is not None:
            result["data"] = self.data
        if self.error is not None:
            result["error"] = self.error
        return result


class WSHandler:
    """WebSocket 请求处理器"""
    
    def __init__(self):
        self._handlers: Dict[str, Callable[[Dict[str, Any]], Awaitable[Any]]] = {}
        self._register_handlers()
    
    def _register_handlers(self):
        """注册所有请求处理器"""
        self._handlers = {
            # 灶台相关
            "get_zones": self._get_zones,
            "get_zone": self._get_zone,
            "create_zone": self._create_zone,
            "update_zone": self._update_zone,
            "delete_zone": self._delete_zone,
            
            # 摄像头相关
            "get_cameras": self._get_cameras,
            "get_camera": self._get_camera,
            "create_camera": self._create_camera,
            "update_camera": self._update_camera,
            "delete_camera": self._delete_camera,
            "get_usb_devices": self._get_usb_devices,
            "preview_camera": self._preview_camera,
            
            # 状态相关
            "get_status": self._get_status,
            "get_device": self._get_device,
            "get_performance": self._get_performance,
            "get_snapshot": self._get_snapshot_image,
            
            # 设置相关
            "get_settings": self._get_settings,
            "update_settings": self._update_settings,
            "get_network": self._get_network,
            "get_remote_config": self._get_remote_config,
            "update_remote_config": self._update_remote_config,
            "verify_remote_login": self._verify_remote_login,
            
            # 控制相关
            "reset_zone": self._reset_zone,
            "toggle_fire": self._toggle_fire,
            
            # 日志相关
            "get_log_files": self._get_log_files,
            "get_log_content": self._get_log_content,
        }
    
    async def handle_request(self, message: dict) -> dict:
        """
        处理 WebSocket 请求
        
        Args:
            message: 请求消息，格式: {type: "request", msg_id: "...", action: "...", params: {...}}
            
        Returns:
            响应消息
        """
        msg_id = message.get("msg_id", str(uuid.uuid4()))
        action = message.get("action", "")
        params = message.get("params", {})
        
        if not action:
            return WSResponse(msg_id, False, error="缺少 action 参数").to_dict()
        
        handler = self._handlers.get(action)
        if not handler:
            return WSResponse(msg_id, False, error=f"未知的 action: {action}").to_dict()
        
        try:
            data = await handler(params)
            return WSResponse(msg_id, True, data=data).to_dict()
        except Exception as e:
            logger.error(f"处理 WebSocket 请求失败: action={action}, error={e}")
            return WSResponse(msg_id, False, error=str(e)).to_dict()
    
    # ==================== 灶台处理器 ====================
    
    async def _get_zones(self, params: dict) -> list:
        """获取所有灶台"""
        from ..zone.state_machine import zone_manager
        zones = zone_manager.get_all_zones()
        return [
            {
                "id": z.zone.id,
                "name": z.zone.name,
                "camera_id": z.zone.camera_id,
                "roi": [list(p) for p in z.zone.roi],
                "enabled": z.zone.enabled
            }
            for z in zones
        ]
    
    async def _get_zone(self, params: dict) -> dict:
        """获取单个灶台"""
        zone_id = params.get("zone_id")
        if not zone_id:
            raise ValueError("缺少 zone_id 参数")
        
        from ..zone.state_machine import zone_manager
        sm = zone_manager.get_zone(zone_id)
        if not sm:
            raise ValueError(f"灶台 '{zone_id}' 不存在")
        
        z = sm.zone
        return {
            "id": z.id,
            "name": z.name,
            "camera_id": z.camera_id,
            "roi": [list(p) for p in z.roi],
            "enabled": z.enabled
        }
    
    async def _create_zone(self, params: dict) -> dict:
        """创建灶台"""
        name = params.get("name")
        camera_id = params.get("camera_id")
        roi = params.get("roi", [])
        enabled = params.get("enabled", True)
        
        if not name:
            raise ValueError("灶台名称不能为空")
        if not camera_id:
            raise ValueError("摄像头ID不能为空")
        
        # 验证摄像头存在
        from ..camera.manager import camera_manager
        if not camera_manager.get_camera(camera_id):
            raise ValueError(f"摄像头 '{camera_id}' 不存在")
        
        # 生成ID
        from ..zone.state_machine import zone_manager
        existing_ids = {z.zone.id for z in zone_manager.get_all_zones()}
        for i in range(1, 100):
            zone_id = f"zone_{i}"
            if zone_id not in existing_ids:
                break
        else:
            zone_id = f"zone_{uuid.uuid4().hex[:8]}"
        
        # 创建配置
        config = ZoneConfig(
            id=zone_id,
            name=name,
            camera_id=camera_id,
            roi=[tuple(p) for p in roi] if roi else [],
            enabled=enabled
        )
        
        # 添加到配置
        config_manager.config.zones.append(config)
        
        # 获取回调
        try:
            from ..main import get_zone_callbacks
            callbacks = get_zone_callbacks()
        except ImportError:
            callbacks = {}
        
        zone_manager.add_zone(config, **callbacks)
        config_manager.save()
        
        return {
            "id": zone_id,
            "name": name,
            "camera_id": camera_id,
            "roi": roi,
            "enabled": enabled
        }
    
    async def _update_zone(self, params: dict) -> dict:
        """更新灶台"""
        zone_id = params.get("zone_id")
        if not zone_id:
            raise ValueError("缺少 zone_id 参数")
        
        from ..zone.state_machine import zone_manager
        sm = zone_manager.get_zone(zone_id)
        if not sm:
            raise ValueError(f"灶台 '{zone_id}' 不存在")
        
        # 更新字段
        if "name" in params:
            sm.zone.name = params["name"]
        if "camera_id" in params:
            sm.zone.camera_id = params["camera_id"]
        if "roi" in params:
            sm.update_config(roi=params["roi"])
        if "enabled" in params:
            sm.zone.enabled = params["enabled"]
        
        # 同步配置
        for cfg in config_manager.config.zones:
            if cfg.id == zone_id:
                if "name" in params:
                    cfg.name = params["name"]
                if "camera_id" in params:
                    cfg.camera_id = params["camera_id"]
                if "roi" in params:
                    cfg.roi = [tuple(p) for p in params["roi"]]
                if "enabled" in params:
                    cfg.enabled = params["enabled"]
                break
        
        config_manager.save()
        
        return {"id": zone_id, "message": "更新成功"}
    
    async def _delete_zone(self, params: dict) -> dict:
        """删除灶台"""
        zone_id = params.get("zone_id")
        if not zone_id:
            raise ValueError("缺少 zone_id 参数")
        
        from ..zone.state_machine import zone_manager
        sm = zone_manager.get_zone(zone_id)
        if not sm:
            raise ValueError(f"灶台 '{zone_id}' 不存在")
        
        zone_name = sm.zone.name
        
        # 从管理器移除
        if zone_id in zone_manager._zones:
            del zone_manager._zones[zone_id]
        if zone_id in zone_manager._fire_states:
            del zone_manager._fire_states[zone_id]
        
        # 从配置移除
        config_manager.config.zones = [z for z in config_manager.config.zones if z.id != zone_id]
        config_manager.save()
        
        return {"id": zone_id, "name": zone_name, "message": "删除成功"}
    
    # ==================== 摄像头处理器 ====================
    
    async def _get_cameras(self, params: dict) -> list:
        """获取所有摄像头"""
        from ..camera.manager import camera_manager
        cameras = camera_manager.get_all_cameras()
        return [
            {
                "id": cam.id,
                "name": cam.name,
                "type": cam.type,
                "status": cam.status.value,
                "width": cam.config.width,
                "height": cam.config.height,
                "fps": cam.config.fps,
                "device": cam.config.device,
                "rtsp_url": cam.config.rtsp_url,
                "username": cam.config.username,
                "password": cam.config.password
            }
            for cam in cameras
        ]
    
    async def _get_camera(self, params: dict) -> dict:
        """获取单个摄像头"""
        camera_id = params.get("camera_id")
        if not camera_id:
            raise ValueError("缺少 camera_id 参数")
        
        from ..camera.manager import camera_manager
        cam = camera_manager.get_camera(camera_id)
        if not cam:
            raise ValueError(f"摄像头 '{camera_id}' 不存在")
        
        return {
            "id": cam.id,
            "name": cam.name,
            "type": cam.type,
            "status": cam.status.value,
            "width": cam.config.width,
            "height": cam.config.height,
            "fps": cam.config.fps,
            "device": cam.config.device,
            "rtsp_url": cam.config.rtsp_url
        }
    
    async def _create_camera(self, params: dict) -> dict:
        """创建摄像头"""
        camera_id = params.get("id")
        name = params.get("name")
        cam_type = params.get("type", "rtsp")
        
        if not camera_id:
            raise ValueError("摄像头ID不能为空")
        if not name:
            raise ValueError("摄像头名称不能为空")
        
        from ..camera.manager import camera_manager
        if camera_manager.get_camera(camera_id):
            raise ValueError(f"摄像头ID '{camera_id}' 已存在")
        
        config = CameraConfig(
            id=camera_id,
            type=cam_type,
            name=name,
            device=params.get("device"),
            rtsp_url=params.get("rtsp_url"),
            username=params.get("username"),
            password=params.get("password"),
            width=params.get("width", 640),
            height=params.get("height", 480),
            fps=params.get("fps", 30)
        )
        
        camera = camera_manager.add_camera(config)
        
        # 后台启动
        def start_async():
            camera.start()
        threading.Thread(target=start_async, daemon=True).start()
        
        config_manager.add_camera(config)
        
        return {
            "id": camera_id,
            "name": name,
            "status": "connecting"
        }
    
    async def _update_camera(self, params: dict) -> dict:
        """更新摄像头"""
        camera_id = params.get("camera_id") or params.get("id")
        if not camera_id:
            raise ValueError("缺少 camera_id 参数")
        
        from ..camera.manager import camera_manager
        
        # 移除旧摄像头
        camera_manager.remove_camera(camera_id)
        config_manager.remove_camera(camera_id)
        
        # 创建新配置
        config = CameraConfig(
            id=camera_id,
            type=params.get("type", "rtsp"),
            name=params.get("name", ""),
            device=params.get("device"),
            rtsp_url=params.get("rtsp_url"),
            username=params.get("username"),
            password=params.get("password"),
            width=params.get("width", 640),
            height=params.get("height", 480),
            fps=params.get("fps", 30)
        )
        
        camera = camera_manager.add_camera(config)
        
        def start_async():
            camera.start()
        threading.Thread(target=start_async, daemon=True).start()
        
        config_manager.add_camera(config)
        
        return {"id": camera_id, "message": "更新成功"}
    
    async def _delete_camera(self, params: dict) -> dict:
        """删除摄像头"""
        camera_id = params.get("camera_id")
        if not camera_id:
            raise ValueError("缺少 camera_id 参数")
        
        from ..camera.manager import camera_manager
        if not camera_manager.remove_camera(camera_id):
            raise ValueError(f"摄像头 '{camera_id}' 不存在")
        
        config_manager.remove_camera(camera_id)
        
        return {"id": camera_id, "message": "删除成功"}
    
    async def _get_usb_devices(self, params: dict) -> list:
        """获取USB设备列表"""
        from ..camera.manager import camera_manager
        return camera_manager.get_available_usb_cameras()
    
    async def _preview_camera(self, params: dict) -> dict:
        """获取摄像头预览帧 (Base64 编码)"""
        import base64
        camera_id = params.get("camera_id")
        if not camera_id:
            raise ValueError("缺少 camera_id 参数")
        
        from ..camera.stream import get_snapshot
        snapshot = get_snapshot(camera_id, quality=80)
        if snapshot:
            b64 = base64.b64encode(snapshot).decode('utf-8')
            return {"image": f"data:image/jpeg;base64,{b64}"}
        raise ValueError("获取预览失败，摄像头可能离线")
    
    async def _get_snapshot_image(self, params: dict) -> dict:
        """获取告警快照图片 (Base64 编码)"""
        import base64
        from pathlib import Path
        
        filename = params.get("filename")
        if not filename:
            raise ValueError("缺少 filename 参数")
        
        # 获取快照目录
        snapshot_dir = Path(__file__).parent.parent.parent / "snapshots"
        file_path = snapshot_dir / filename
        
        if not file_path.exists():
            raise ValueError(f"快照文件不存在: {filename}")
        
        with open(file_path, "rb") as f:
            data = f.read()
        
        b64 = base64.b64encode(data).decode('utf-8')
        # 根据文件扩展名确定 MIME 类型
        ext = file_path.suffix.lower()
        if ext in ['.jpg', '.jpeg']:
            mime = 'image/jpeg'
        elif ext == '.png':
            mime = 'image/png'
        else:
            mime = 'application/octet-stream'
        
        return {"image": f"data:{mime};base64,{b64}", "filename": filename}
    
    async def _toggle_fire(self, params: dict) -> dict:
        """模拟火焰开关"""
        zone_id = params.get("zone_id")
        is_on = params.get("is_on", False)
        
        if not zone_id:
            raise ValueError("缺少 zone_id 参数")
        
        from ..zone.state_machine import zone_manager
        sm = zone_manager.get_zone(zone_id)
        if not sm:
            raise ValueError(f"灶台 '{zone_id}' 不存在")
        
        # 更新模拟火焰状态
        zone_manager._fire_states[zone_id] = is_on
        
        return {"zone_id": zone_id, "is_on": is_on, "message": "火焰状态已更新"}
    
    # ==================== 状态处理器 ====================
    
    async def _get_status(self, params: dict) -> list:
        """获取所有灶台状态"""
        from ..zone.state_machine import zone_manager
        return zone_manager.get_all_status()
    
    async def _get_device(self, params: dict) -> dict:
        """获取设备信息"""
        import platform
        config = config_manager.config
        return {
            "name": config.system.name,
            "version": config.system.version,
            "device_id": config.system.device_id,
            "platform": platform.system(),
            "python_version": platform.python_version()
        }
    
    async def _get_performance(self, params: dict) -> dict:
        """获取性能指标"""
        from ..utils.performance import performance_monitor
        stats = performance_monitor.get_stats_dict()
        return {
            "engine": config_manager.config.inference.engine,
            "model": config_manager.config.inference.model_path,
            **stats
        }
    
    # ==================== 设置处理器 ====================
    
    async def _get_settings(self, params: dict) -> dict:
        """获取系统设置"""
        category = params.get("category", "all")
        config = config_manager.config
        
        result = {}
        
        if category in ["all", "alarm"]:
            result["alarm"] = {
                "warning_time": config.alarm.warning_time,
                "alarm_time": config.alarm.alarm_time,
                "action_time": config.alarm.action_time,
                "broadcast_interval": config.alarm.broadcast_interval,
                "warning_message": config.alarm.warning_message,
                "alarm_message": config.alarm.alarm_message,
                "action_message": config.alarm.action_message
            }
        
        if category in ["all", "system"]:
            result["system"] = {
                "name": config.system.name,
                "version": config.system.version,
                "device_id": config.system.device_id,
                "debug": config.system.debug
            }
        
        if category in ["all", "tts"]:
            result["tts"] = {
                "enabled": config.tts.enabled,
                "engine": config.tts.engine,
                "audio_dir": config.tts.audio_dir,
                "idle_timeout": config.tts.idle_timeout
            }
        
        return result
    
    async def _update_settings(self, params: dict) -> dict:
        """更新系统设置"""
        category = params.get("category")
        settings = params.get("settings", {})
        
        config = config_manager.config
        
        if category == "alarm":
            alarm = config.alarm
            if "warning_time" in settings:
                alarm.warning_time = settings["warning_time"]
            if "alarm_time" in settings:
                alarm.alarm_time = settings["alarm_time"]
            if "action_time" in settings:
                alarm.action_time = settings["action_time"]
            if "broadcast_interval" in settings:
                alarm.broadcast_interval = settings["broadcast_interval"]
            if "warning_message" in settings:
                alarm.warning_message = settings["warning_message"]
            if "alarm_message" in settings:
                alarm.alarm_message = settings["alarm_message"]
            if "action_message" in settings:
                alarm.action_message = settings["action_message"]
        
        config_manager.save()
        return {"message": "设置已更新"}
    
    async def _get_network(self, params: dict) -> dict:
        """获取网络状态"""
        from ..utils.network_monitor import network_monitor
        status = network_monitor.update_status()
        return status.to_dict()
    
    async def _get_remote_config(self, params: dict) -> dict:
        """获取远程服务器配置"""
        remote = config_manager.config.remote
        
        result = {
            "enabled": remote.enabled,
            "server_url": remote.server_url,
            "websocket_path": remote.websocket_path,
            "login_path": remote.login_path,
            "username": remote.username,
            "has_token": bool(remote.token),
            "is_connected": False,
            "is_connecting": False,
            "last_error": "",
            "reconnect_attempts": 0
        }
        
        try:
            from .websocket_client import remote_ws_client
            state = remote_ws_client.state
            result["is_connected"] = state.is_connected
            result["is_connecting"] = state.is_connecting
            result["last_error"] = state.last_error
            result["reconnect_attempts"] = state.reconnect_attempts
        except Exception:
            pass
        
        return result
    
    async def _update_remote_config(self, params: dict) -> dict:
        """更新远程服务器配置"""
        remote = config_manager.config.remote
        
        if "enabled" in params:
            remote.enabled = params["enabled"]
        if "server_url" in params:
            remote.server_url = params["server_url"]
        if "websocket_path" in params:
            remote.websocket_path = params["websocket_path"]
        if "login_path" in params:
            remote.login_path = params["login_path"]
        if "username" in params:
            remote.username = params["username"]
        if "password" in params and params["password"]:
            remote.password = params["password"]
            remote.token = ""  # 清除旧Token
        
        config_manager.save()
        
        # 重新连接
        if remote.enabled:
            try:
                from .websocket_client import remote_ws_client
                import asyncio
                await remote_ws_client.stop()
                asyncio.create_task(remote_ws_client.start())
            except Exception as e:
                return {"message": f"配置已保存，但连接启动失败: {e}"}
        
        return {"message": "远程配置已更新"}
    
    async def _verify_remote_login(self, params: dict) -> dict:
        """校验远程登录"""
        import aiohttp
        from urllib.parse import urlparse
        
        server_url = params.get("server_url", "").strip()
        login_path = params.get("login_path", "/login").strip()
        username = params.get("username", "")
        password = params.get("password", "")
        
        if not server_url:
            raise ValueError("服务器地址不能为空")
        
        if not server_url.startswith(('http://', 'https://')):
            server_url = 'http://' + server_url
        
        parsed = urlparse(server_url)
        if not login_path.startswith('/'):
            login_path = '/' + login_path
        
        login_url = f"{parsed.scheme}://{parsed.netloc}{login_path}"
        
        timeout = aiohttp.ClientTimeout(total=10)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            payload = {"username": username, "password": password}
            async with session.post(login_url, json=payload) as response:
                if response.status == 200:
                    data = await response.json()
                    if data.get('code') == 200:
                        token = data.get('token', '')
                        if token:
                            # 保存配置
                            remote = config_manager.config.remote
                            remote.server_url = params.get("server_url", "")
                            remote.login_path = login_path
                            remote.username = username
                            remote.password = password
                            remote.token = token
                            config_manager.save()
                            return {"success": True, "message": "校验成功"}
                        else:
                            raise ValueError("响应中无 Token")
                    else:
                        raise ValueError(data.get('msg', '登录失败'))
                elif response.status == 401:
                    raise ValueError("用户名或密码错误")
                else:
                    text = await response.text()
                    raise ValueError(f"HTTP {response.status}: {text[:100]}")
    
    # ==================== 控制处理器 ====================
    
    async def _reset_zone(self, params: dict) -> dict:
        """重置灶台"""
        zone_id = params.get("zone_id")
        if not zone_id:
            raise ValueError("缺少 zone_id 参数")
        
        from ..zone.state_machine import zone_manager
        sm = zone_manager.get_zone(zone_id)
        if not sm:
            raise ValueError(f"灶台 '{zone_id}' 不存在")
        
        sm.reset()
        return {"id": zone_id, "message": "灶台已重置"}
    
    # ==================== 日志处理器 ====================
    
    async def _get_log_files(self, params: dict) -> list:
        """获取日志文件列表"""
        from ..utils.logger import event_logger
        log_dir = event_logger._log_dir
        if not log_dir or not log_dir.exists():
            return []
        
        files = []
        for f in sorted(log_dir.glob("*.log"), reverse=True):
            files.append({
                "name": f.name,
                "size": f.stat().st_size,
                "mtime": f.stat().st_mtime
            })
        return files
    
    async def _get_log_content(self, params: dict) -> dict:
        """读取日志内容"""
        from ..utils.logger import event_logger
        filename = params.get("filename")
        lines = params.get("lines", 200)
        
        log_dir = event_logger._log_dir
        if not log_dir or not log_dir.exists():
            return {"content": "日志目录不存在"}
        
        if not filename:
            files = sorted(log_dir.glob("*.log"), reverse=True)
            if not files:
                return {"content": "暂无日志文件"}
            file_path = files[0]
        else:
            file_path = log_dir / filename
            if not file_path.exists():
                return {"content": "日志文件不存在"}
        
        with open(file_path, "r", encoding="utf-8") as f:
            all_lines = f.readlines()
            content = "".join(all_lines[-lines:])
            return {
                "filename": file_path.name,
                "content": content,
                "total_lines": len(all_lines)
            }


# 全局处理器实例
ws_handler = WSHandler()
