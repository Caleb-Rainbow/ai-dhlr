"""
WebSocket 请求处理器
处理前端通过 WebSocket 发送的所有请求，实现请求-响应模式
"""
import time
import uuid
import os
import threading
from typing import Dict, Any, Optional, Callable, Awaitable, List
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
            "get_system": self._get_system,
            
            # 设置相关
            "get_settings": self._get_settings,
            "update_settings": self._update_settings,
            "set_device_id": self._set_device_id,
            "get_network": self._get_network,
            "get_remote_config": self._get_remote_config,
            "update_remote_config": self._update_remote_config,
            "verify_remote_login": self._verify_remote_login,
            # 音量相关
            "get_volume": self._get_volume,
            "set_volume": self._set_volume,
            # 监测模式相关
            "get_zone_mode": self._get_zone_mode,
            "set_zone_mode": self._set_zone_mode,

            # 控制相关
            "reset_zone": self._reset_zone,
            "toggle_fire": self._toggle_fire,
            
            # 日志相关
            "get_log_files": self._get_log_files,
            "get_log_content": self._get_log_content,
            
            # 串口相关
            "get_serial_ports": self._get_serial_ports,
            "get_serial_config": self._get_serial_config,
            "update_serial_config": self._update_serial_config,
            "get_currents": self._get_currents,
            "get_lora_config": self._get_lora_config,
            "set_lora_config": self._set_lora_config,
            "set_serial_debug": self._set_serial_debug,
            
            # 巡检相关
            "start_patrol": self._start_patrol,
            "stop_patrol": self._stop_patrol,
            "patrol_self_check": self._patrol_self_check,
            "patrol_alarm_demo": self._patrol_alarm_demo,
            "patrol_force_warning": self._patrol_force_warning,
            "patrol_force_alarm": self._patrol_force_alarm,
            "patrol_force_cutoff": self._patrol_force_cutoff,
            "get_patrol_status": self._get_patrol_status,
            # 单灶台巡检操作
            "patrol_check_person": self._patrol_check_person,
            "patrol_check_fire": self._patrol_check_fire,
            "patrol_cutoff_zone": self._patrol_cutoff_zone,
            
            # GPIO 相关
            "get_gpio_pins": self._get_gpio_pins,
            "get_gpio_config": self._get_gpio_config,
            "update_gpio_config": self._update_gpio_config,

            # USB OTG 模式
            "get_usb_otg_mode": self._get_usb_otg_mode,
            "set_usb_otg_mode": self._set_usb_otg_mode,

            # 开机自启热点开关
            "get_hotspot_autostart": self._get_hotspot_autostart,
            "set_hotspot_autostart": self._set_hotspot_autostart,

            # 系统更新
            "trigger_update": self._trigger_update,

            # 依赖安装
            "install_dependencies": self._install_dependencies,
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
        
        # 获取电流值
        currents = {}
        try:
            from ..serial_port.serial_manager import serial_manager
            currents = serial_manager.get_all_currents()
        except Exception:
            pass
        
        result = []
        for z in zones:
            zone_data = {
                "id": z.zone.id,
                "name": z.zone.name,
                "camera_id": z.zone.camera_id,
                "camera_ids": list(z.zone.camera_ids),
                "roi": [list(p) for p in z.zone.roi],
                "enabled": z.zone.enabled
            }
            # 从配置获取serial_index和fire_current_threshold
            for cfg in config_manager.config.zones:
                if cfg.id == z.zone.id:
                    zone_data["serial_index"] = cfg.serial_index
                    zone_data["fire_current_threshold"] = cfg.fire_current_threshold
                    zone_data["current_value"] = currents.get(z.zone.id, 0)
                    break
            result.append(zone_data)
        return result
    
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
            "camera_ids": list(z.camera_ids),
            "roi": [list(p) for p in z.roi],
            "enabled": z.enabled
        }
    
    async def _create_zone(self, params: dict) -> dict:
        """创建灶台"""
        name = params.get("name")
        camera_id = params.get("camera_id")
        camera_ids = list(params.get("camera_ids", []) or [])
        roi = params.get("roi", [])
        enabled = params.get("enabled", True)
        serial_index = params.get("serial_index", 0)
        fire_current_threshold = params.get("fire_current_threshold", 100)
        enable_temp_sensor = params.get("enable_temp_sensor", False)  # 是否启用温度传感器

        if not name:
            raise ValueError("灶台名称不能为空")

        # 摄像头校验：优先多摄像头（不分区模式）；为空则回退单摄像头（向后兼容）
        from ..camera.manager import camera_manager
        if camera_ids:
            for cid in camera_ids:
                if not camera_manager.get_camera(cid):
                    raise ValueError(f"摄像头 '{cid}' 不存在")
            # 兼容：未单独提供 camera_id 时取首个
            if not camera_id:
                camera_id = camera_ids[0]
        else:
            if not camera_id:
                raise ValueError("摄像头ID不能为空")
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
        
        # 温度传感器自动分配逻辑
        temp_sensor_address = None
        if enable_temp_sensor:
            try:
                from ..serial_port.serial_manager import serial_manager
                # 分配一个新地址
                new_address = serial_manager.allocate_sensor_address()
                if new_address == 0:
                    raise ValueError("无可用的传感器地址，请先解绑其他传感器")
                
                # 发送命令将传感器从默认地址(123)修改为新地址
                # 注意：用户需确保此时只接入了一个传感器
                DEFAULT_SENSOR_ADDRESS = 123
                success = serial_manager.assign_sensor_address(DEFAULT_SENSOR_ADDRESS, new_address)
                if success:
                    temp_sensor_address = new_address
                    logger.info(f"温度传感器已分配地址: {DEFAULT_SENSOR_ADDRESS} -> {new_address}")
                else:
                    raise ValueError("发送传感器配置命令失败，串口可能未连接")
            except ImportError:
                raise ValueError("串口模块不可用")
        
        # 创建配置
        config = ZoneConfig(
            id=zone_id,
            name=name,
            camera_id=camera_id,
            roi=[tuple(p) for p in roi] if roi else [],
            enabled=enabled,
            serial_index=serial_index,
            fire_current_threshold=fire_current_threshold,
            temp_sensor_address=temp_sensor_address,
            camera_ids=camera_ids
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
        
        # 注册到串口管理器
        try:
            from ..serial_port.serial_manager import serial_manager
            serial_manager.register_zone(zone_id, serial_index, fire_current_threshold)
            # 注册温度传感器（如果已分配地址）
            if temp_sensor_address is not None:
                serial_manager.register_temperature_sensor(zone_id, temp_sensor_address)
        except Exception:
            pass

        return {
            "id": zone_id,
            "name": name,
            "camera_id": camera_id,
            "camera_ids": camera_ids,
            "roi": roi,
            "enabled": enabled,
            "serial_index": serial_index,
            "fire_current_threshold": fire_current_threshold,
            "temp_sensor_address": temp_sensor_address,
            "temp_sensor_enabled": temp_sensor_address is not None
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
        if "camera_ids" in params:
            new_camera_ids = list(params["camera_ids"] or [])
            from ..camera.manager import camera_manager
            for cid in new_camera_ids:
                if not camera_manager.get_camera(cid):
                    raise ValueError(f"摄像头 '{cid}' 不存在")
            sm.zone.camera_ids = new_camera_ids
        if "roi" in params:
            sm.update_config(roi=params["roi"])
        if "enabled" in params:
            sm.zone.enabled = params["enabled"]
            # 停用灶台时强制重置状态
            if not params["enabled"]:
                sm.force_idle()
        
        # 处理温度传感器开关
        enable_temp_sensor = params.get("enable_temp_sensor")
        new_temp_sensor_address = None
        
        # 同步配置
        old_temp_sensor_address = None
        for cfg in config_manager.config.zones:
            if cfg.id == zone_id:
                old_temp_sensor_address = cfg.temp_sensor_address
                if "name" in params:
                    cfg.name = params["name"]
                if "camera_id" in params:
                    cfg.camera_id = params["camera_id"]
                if "camera_ids" in params:
                    cfg.camera_ids = list(params["camera_ids"] or [])
                if "roi" in params:
                    cfg.roi = [tuple(p) for p in params["roi"]]
                if "enabled" in params:
                    cfg.enabled = params["enabled"]
                if "serial_index" in params:
                    cfg.serial_index = params["serial_index"]
                if "fire_current_threshold" in params:
                    cfg.fire_current_threshold = params["fire_current_threshold"]
                
                # 处理温度传感器启用/禁用
                if enable_temp_sensor is not None:
                    try:
                        from ..serial_port.serial_manager import serial_manager
                        
                        if enable_temp_sensor and old_temp_sensor_address is None:
                            # 启用温度传感器：自动分配地址
                            new_address = serial_manager.allocate_sensor_address()
                            if new_address == 0:
                                raise ValueError("无可用的传感器地址")
                            
                            # 发送命令修改传感器地址 (123 -> 新地址)
                            DEFAULT_SENSOR_ADDRESS = 123
                            success = serial_manager.assign_sensor_address(DEFAULT_SENSOR_ADDRESS, new_address)
                            if success:
                                new_temp_sensor_address = new_address
                                cfg.temp_sensor_address = new_address
                                serial_manager.register_temperature_sensor(zone_id, new_address)
                                logger.info(f"温度传感器已分配地址: {DEFAULT_SENSOR_ADDRESS} -> {new_address}")
                            else:
                                raise ValueError("发送传感器配置命令失败")
                        
                        elif not enable_temp_sensor and old_temp_sensor_address is not None:
                            # 禁用温度传感器：解绑
                            serial_manager.unregister_temperature_sensor(zone_id)
                            cfg.temp_sensor_address = None
                            logger.info(f"温度传感器已解绑: zone={zone_id}")
                        
                    except ImportError:
                        raise ValueError("串口模块不可用")
                
                # 更新其他串口配置
                try:
                    from ..serial_port.serial_manager import serial_manager
                    serial_manager.update_zone_config(
                        zone_id,
                        serial_index=params.get("serial_index"),
                        fire_threshold=params.get("fire_current_threshold")
                    )
                except Exception:
                    pass
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

        if not name:
            raise ValueError("摄像头名称不能为空")

        from ..camera.manager import camera_manager

        # 如果未提供ID，则自动生成（从0开始自增）
        if not camera_id:
            existing_ids = {cam.id for cam in camera_manager.get_all_cameras()}
            next_id = 0
            while str(next_id) in existing_ids:
                next_id += 1
            camera_id = str(next_id)

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
        """获取摄像头预览帧 (Base64 编码)

        使用帧缓存优化多客户端预览场景，减少重复编码开销。
        """
        camera_id = params.get("camera_id")
        if not camera_id:
            raise ValueError("缺少 camera_id 参数")

        from ..camera.manager import camera_manager
        from ..camera.frame_cache import frame_cache

        # 检查摄像头状态
        camera = camera_manager.get_camera(camera_id)
        if not camera:
            raise ValueError(f"摄像头 {camera_id} 不存在")

        # 如果摄像头离线或错误状态，尝试触发重连
        if not camera.is_online:
            logger.info(f"预览时检测到摄像头离线，触发重连: {camera_id}")
            camera.reconnect()
            # 等待短暂时间让重连开始
            import asyncio
            await asyncio.sleep(0.5)
            # 再次检查状态
            if not camera.is_online:
                raise ValueError(f"摄像头离线，正在尝试重连...")

        # 获取原始帧，如果帧缓冲区为空则等待
        frame = camera.get_snapshot()

        # 如果帧为空，等待帧可用（最多等待2秒）
        if frame is None:
            logger.debug(f"帧缓冲区为空，等待帧可用: {camera_id}")
            import asyncio
            for i in range(20):  # 最多等待2秒
                await asyncio.sleep(0.1)
                frame = camera.get_snapshot()
                if frame is not None:
                    logger.debug(f"帧可用，等待了 {(i+1) * 100}ms: {camera_id}")
                    break

        if frame is None:
            logger.warning(f"获取预览失败，帧缓冲区为空: {camera_id}")
            raise ValueError("获取预览失败，摄像头可能正在初始化")

        # 检查帧尺寸是否有效
        if frame.shape[0] == 0 or frame.shape[1] == 0:
            logger.warning(f"帧尺寸无效: {camera_id}, shape={frame.shape}")
            raise ValueError("获取预览失败，帧尺寸无效")

        # 使用帧缓存进行编码（减少多客户端重复编码开销）
        result = frame_cache.get_or_encode(camera_id, frame, quality=80)
        if result:
            base64_str, from_cache = result
            if from_cache:
                logger.debug(f"预览帧从缓存获取: {camera_id}")
            return {"image": f"data:image/jpeg;base64,{base64_str}"}

        logger.error(f"帧编码失败: {camera_id}")
        raise ValueError("获取预览失败，帧编码错误")
    
    async def _get_snapshot_image(self, params: dict) -> dict:
        """获取告警快照图片 (Base64 编码)。

        filename 不可信（可经 BLE 无鉴权链路或 LAN 下发）：resolve + relative_to 防
        `../../etc/shadow` 路径穿越读任意文件（主应用 root）；并限大小防 WS 溢出/DoS。"""
        import base64
        from pathlib import Path

        filename = params.get("filename")
        if not filename or not isinstance(filename, str):
            raise ValueError("缺少 filename 参数")
        if "/" in filename or "\\" in filename or filename.startswith("."):
            raise ValueError("非法 filename")

        snapshot_dir = (Path(__file__).parent.parent.parent / "snapshots").resolve()
        file_path = (snapshot_dir / filename).resolve()
        try:  # 防穿越：解析后必须仍在 snapshot_dir 内
            file_path.relative_to(snapshot_dir)
        except ValueError:
            raise ValueError("filename escapes snapshot dir")

        if not file_path.exists():
            raise ValueError(f"快照文件不存在: {filename}")

        # 大小上限：防读超大文件致 base64 撑爆 WS（max_size=8MB）/DoS
        MAX_SNAPSHOT_BYTES = 2 * 1024 * 1024
        size = file_path.stat().st_size
        if size > MAX_SNAPSHOT_BYTES:
            raise ValueError(f"快照过大: {size}")

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
            "python_version": platform.python_version(),
            "zone_mode": config.system.zone_mode
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
    
    async def _get_system(self, params: dict) -> dict:
        """获取设备系统运行态遥测（CPU/NPU/GPU/内存/存储/温度/版本/uptime/负载/流量）。

        纯读 sysfs/proc/psutil，无副作用。每项独立 try/except：单项读取失败给 null，
        不影响其他字段返回（容错风格同 _get_npu_load）。
        App 运维面板经 BLE rpc 调用，一次返回全部指标，避免多次往返占用 GATT 通道。
        """
        from ..utils.performance import performance_monitor
        stats = performance_monitor.get_stats_dict()

        def gpu_load() -> Optional[float]:
            # devfreq gpu 节点 load 格式 "0@800000000Hz"，取 @ 前
            try:
                import glob
                for path in glob.glob("/sys/class/devfreq/*gpu*/load"):
                    with open(path) as f:
                        return float(f.read().strip().split("@")[0])
            except Exception:
                return None
            return None

        def temperatures() -> Optional[Dict[str, float]]:
            try:
                base = "/sys/class/thermal"
                result: Dict[str, float] = {}
                for tz in os.listdir(base):
                    if not tz.startswith("thermal_zone"):
                        continue
                    try:
                        name = open(f"{base}/{tz}/type").read().strip()
                        result[name] = float(open(f"{base}/{tz}/temp").read().strip()) / 1000.0
                    except Exception:
                        continue
                return result or None
            except Exception:
                return None

        def memory() -> Optional[Dict[str, float]]:
            # 系统级内存，区别于 performance_monitor 的进程 RSS
            try:
                import psutil
                vm = psutil.virtual_memory()
                return {
                    "total_mb": round(vm.total / (1024 * 1024), 1),
                    "available_mb": round(vm.available / (1024 * 1024), 1),
                    "used_percent": round(vm.percent, 1),
                }
            except Exception:
                return None

        def disk() -> Optional[Dict[str, float]]:
            try:
                import psutil
                du = psutil.disk_usage("/")
                return {
                    "total_gb": round(du.total / (1024 ** 3), 1),
                    "used_gb": round(du.used / (1024 ** 3), 1),
                    "free_gb": round(du.free / (1024 ** 3), 1),
                    "used_percent": round(du.percent, 1),
                }
            except Exception:
                return None

        def version_info() -> Dict[str, Optional[str]]:
            result: Dict[str, Optional[str]] = {}
            try:
                import platform
                u = platform.uname()
                result["os"] = f"{u.system} {u.release} {u.machine}".strip()
            except Exception:
                result["os"] = None
            try:
                import sys
                result["python"] = sys.version.split()[0]
            except Exception:
                result["python"] = None
            try:
                result["app"] = config_manager.config.system.version
            except Exception:
                result["app"] = None
            try:
                import rknnlite
                result["rknn"] = getattr(rknnlite, "__version__", None)
            except Exception:
                result["rknn"] = None
            return result

        def uptime_seconds() -> Optional[float]:
            try:
                with open("/proc/uptime") as f:
                    return round(float(f.read().split()[0]), 1)
            except Exception:
                return None

        def load_average() -> Optional[List[float]]:
            try:
                return [round(x, 2) for x in os.getloadavg()]
            except Exception:
                return None

        def network() -> Optional[Dict[str, float]]:
            try:
                import psutil
                nio = psutil.net_io_counters()
                return {
                    "rx_mb": round(nio.bytes_recv / (1024 * 1024), 1),
                    "tx_mb": round(nio.bytes_sent / (1024 * 1024), 1),
                }
            except Exception:
                return None

        return {
            "cpu_percent": stats.get("cpu_percent"),
            "npu_load": stats.get("npu_load"),
            "gpu_load": gpu_load(),
            "temperatures": temperatures(),
            "memory": memory(),
            "process_memory_mb": stats.get("memory_mb"),
            "disk": disk(),
            "inference": {
                "engine": config_manager.config.inference.engine,
                "model": config_manager.config.inference.model_path,
                "fps": stats.get("fps"),
                "inference_time_ms": stats.get("inference_time_ms"),
            },
            "version": version_info(),
            "uptime_seconds": uptime_seconds(),
            "load_average": load_average(),
            "network": network(),
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
                "action_message": config.alarm.action_message,
                "temp_alarm_threshold": config.alarm.temp_alarm_threshold,
                "temp_alarm_message": config.alarm.temp_alarm_message
            }
        
        if category in ["all", "system"]:
            result["system"] = {
                "name": config.system.name,
                "version": config.system.version,
                "device_id": config.system.device_id,
                "debug": config.system.debug,
                "zone_mode": config.system.zone_mode,
                "hotspot_autostart": config.hotspot.auto_start_on_boot
            }

        if category in ["all", "voice"]:
            result["voice"] = {
                "enabled": config.voice.enabled,
                "volume": config.voice.volume
            }

        return result
    
    async def _update_settings(self, params: dict) -> dict:
        """更新系统设置"""
        category = params.get("category")
        settings = params.get("settings", {})
        
        config = config_manager.config
        
        if category == "alarm":
            alarm = config.alarm
            # 范围校验：BLE 无鉴权端可下发设置，防极端值实质禁用报警
            # （如 action_time=99999 让切电永不触发、temp_alarm_threshold=0 恒报或失敏）。
            INT_BOUNDS = {  # key: (lo, hi)
                "warning_time": (0, 3600),
                "alarm_time": (0, 3600),
                "action_time": (0, 3600),
                "broadcast_interval": (0, 3600),
            }
            for key, (lo, hi) in INT_BOUNDS.items():
                if key in settings:
                    try:
                        v = int(settings[key])
                    except (TypeError, ValueError):
                        raise ValueError(f"{key} 必须是整数")
                    if not (lo <= v <= hi):
                        raise ValueError(f"{key} 越界，允许 {lo}..{hi}")
                    setattr(alarm, key, v)
            if "temp_alarm_threshold" in settings:
                try:
                    t = float(settings["temp_alarm_threshold"])
                except (TypeError, ValueError):
                    raise ValueError("temp_alarm_threshold 必须是数值")
                if not (0 <= t <= 200):
                    raise ValueError("temp_alarm_threshold 越界，允许 0..200")
                alarm.temp_alarm_threshold = t
            # 文本字段：限长度防滥用
            for key in ("warning_message", "alarm_message", "action_message", "temp_alarm_message"):
                if key in settings:
                    setattr(alarm, key, str(settings[key])[:200])
        
        config_manager.save()
        return {"message": "设置已更新"}
    
    async def _set_device_id(self, params: dict) -> dict:
        """设置设备ID"""
        device_id = params.get("device_id", "").strip()

        if not device_id:
            raise ValueError("设备ID不能为空")

        # 保存到配置
        config_manager.config.system.device_id = device_id
        config_manager.save()

        logger.info(f"设备ID已更新为: {device_id}")

        return {"device_id": device_id, "message": "设备ID已更新"}
    
    async def _get_volume(self, params: dict) -> dict:
        """获取当前语音音量"""
        config = config_manager.config
        return {"volume": config.voice.volume}
    
    async def _set_volume(self, params: dict) -> dict:
        """设置语音音量"""
        volume = params.get("volume")
        
        if volume is None:
            raise ValueError("缺少 volume 参数")
        
        # 验证范围
        volume = float(volume)
        if volume < 0 or volume > 1:
            raise ValueError("音量必须在 0.0 到 1.0 之间")
        
        # 更新配置
        config_manager.config.voice.volume = volume
        config_manager.save()
        
        # 更新播放器音量
        try:
            from ..output.voice import voice_player
            voice_player.set_volume(volume)
        except Exception as e:
            logger.warning(f"更新播放器音量失败: {e}")
        
        return {"volume": volume, "message": "音量已更新"}
    
    async def _get_zone_mode(self, params: dict) -> dict:
        """获取当前监测模式"""
        config = config_manager.config
        return {
            "zone_mode": config.system.zone_mode,
            "zone_count": len(config.zones)
        }
    
    async def _set_zone_mode(self, params: dict) -> dict:
        """设置监测模式
        
        Args:
            zone_mode: "zoned" (分区监测) 或 "single" (不分区监测)
        """
        zone_mode = params.get("zone_mode")
        
        if zone_mode not in ["zoned", "single"]:
            raise ValueError("zone_mode 必须是 'zoned' 或 'single'")
        
        config = config_manager.config
        old_mode = config.system.zone_mode
        
        # 模式切换时不再要求删除灶台，允许保留灶台配置
        # 不分区模式下使用灶台配置中的 serial_index 和 fire_current_threshold

        # 保存配置
        config.system.zone_mode = zone_mode
        config_manager.save()
        
        logger.info(f"监测模式已切换: {old_mode} -> {zone_mode}")
        
        return {
            "zone_mode": zone_mode,
            "message": f"已切换到{'分区监测' if zone_mode == 'zoned' else '不分区监测'}模式"
        }

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
        
        # 限制返回数量：日志文件列表经 BLE 单帧 rpc 下发（< 8192 硬上限），文件过多（retention
        # 失效累积）会撑爆单帧（service.py 超帧保护会回 "response too large" 致功能失败）。
        # 按名倒序（新在前）取最近 MAX_LOG_FILES 个——排查主要看近期，足够覆盖。
        MAX_LOG_FILES = 50
        files = []
        for f in sorted(log_dir.glob("*.log"), reverse=True)[:MAX_LOG_FILES]:
            files.append({
                "name": f.name,
                "size": f.stat().st_size,
                "mtime": f.stat().st_mtime,
            })
        return files
    
    async def _get_log_content(self, params: dict) -> dict:
        """读取日志内容，支持分页"""
        from ..utils.logger import event_logger
        filename = params.get("filename")
        page = max(1, params.get("page", 1))
        # page_size 下限放宽至 20：App 无感分页（向上滑到顶 prepend 更旧页）传小 page_size（50 行），
        # 使单页 < 6KB 不触发截断，分页连续不丢行（截断仅对超长堆栈行防御）。
        page_size = min(500, max(20, params.get("page_size", 100)))

        log_dir = event_logger._log_dir
        if not log_dir or not log_dir.exists():
            return {"content": "日志目录不存在", "total_lines": 0, "page": 1, "total_pages": 0}

        if not filename:
            files = sorted(log_dir.glob("*.log"), reverse=True)
            if not files:
                return {"content": "暂无日志文件", "total_lines": 0, "page": 1, "total_pages": 0}
            file_path = files[0]
        else:
            # 防路径穿越（BLE filename 不可信，主应用 root）：字符级拒绝分隔符/前导点 + 后缀 .log
            # + resolve 必须仍在 log_dir 内（双层防护，照 _get_snapshot_image 同类修复）。
            from pathlib import Path
            if (
                not isinstance(filename, str)
                or "/" in filename or "\\" in filename or filename.startswith(".")
                or not filename.endswith(".log")
            ):
                raise ValueError("非法 filename")
            log_dir_resolved = log_dir.resolve()
            file_path = (log_dir / filename).resolve()
            try:
                file_path.relative_to(log_dir_resolved)
            except ValueError:
                raise ValueError("filename escapes log dir")
            if not file_path.exists():
                return {"content": "日志文件不存在", "total_lines": 0, "page": 1, "total_pages": 0, "truncated": False}

        with open(file_path, "r", encoding="utf-8") as f:
            all_lines = f.readlines()

        total_lines = len(all_lines)
        total_pages = max(1, (total_lines + page_size - 1) // page_size)
        page = min(page, total_pages)
        start = total_lines - page * page_size
        end = total_lines - (page - 1) * page_size
        start = max(0, start)
        page_lines = all_lines[start:end]
        content = "".join(page_lines)

        # 字节上限：单页内容经 BLE JSON 单帧下发，须 < 8192 硬上限。超 MAX 按字节取末尾（保留最新），
        # errors="ignore" 丢弃开头可能的多字节残尾。truncated 标记告知 App 如实提示（数据诚实）。
        MAX_LOG_CONTENT_BYTES = 6 * 1024  # 6144
        truncated = False
        encoded = content.encode("utf-8")
        if len(encoded) > MAX_LOG_CONTENT_BYTES:
            content = encoded[-MAX_LOG_CONTENT_BYTES:].decode("utf-8", errors="ignore")
            truncated = True

        return {
            "filename": file_path.name,
            "content": content,
            "total_lines": total_lines,
            "page": page,
            "page_size": page_size,
            "total_pages": total_pages,
            "truncated": truncated,
        }
    
    # ==================== 串口处理器 ====================
    
    async def _get_serial_ports(self, params: dict) -> list:
        """获取系统可用的串口列表（不依赖 pyserial）"""
        import platform
        import os
        
        ports = []
        system = platform.system()
        
        try:
            if system == "Linux":
                # Linux: 扫描 /dev 目录下的串口设备
                dev_patterns = [
                    "/dev/ttyS",    # 内置串口
                    "/dev/ttyUSB",  # USB 转串口
                    "/dev/ttyACM",  # ACM 设备
                    "/dev/ttyAMA",  # 树莓派等 ARM 设备
                    "/dev/serial",  # 通用串口符号链接
                ]
                
                if os.path.exists("/dev"):
                    for entry in os.listdir("/dev"):
                        dev_path = f"/dev/{entry}"
                        for pattern in dev_patterns:
                            if dev_path.startswith(pattern):
                                # 尝试获取设备信息
                                description = entry
                                try:
                                    # 尝试读取 /sys 下的设备信息
                                    sys_path = f"/sys/class/tty/{entry}/device/driver"
                                    if os.path.exists(sys_path):
                                        driver = os.path.basename(os.readlink(sys_path))
                                        description = f"{entry} ({driver})"
                                except Exception:
                                    pass
                                
                                ports.append({
                                    "device": dev_path,
                                    "name": entry,
                                    "description": description,
                                    "hwid": ""
                                })
                                break
                
            elif system == "Windows":
                # Windows: 通过注册表查询串口
                try:
                    import winreg
                    key = winreg.OpenKey(
                        winreg.HKEY_LOCAL_MACHINE,
                        r"HARDWARE\DEVICEMAP\SERIALCOMM"
                    )
                    i = 0
                    while True:
                        try:
                            name, value, _ = winreg.EnumValue(key, i)
                            ports.append({
                                "device": value,  # 如 "COM3"
                                "name": value,
                                "description": name,  # 如 "\Device\Serial0"
                                "hwid": ""
                            })
                            i += 1
                        except WindowsError:
                            break
                    winreg.CloseKey(key)
                except Exception as e:
                    logger.warning(f"Windows 串口枚举失败: {e}")
            
            elif system == "Darwin":
                # macOS: 扫描 /dev 目录
                if os.path.exists("/dev"):
                    for entry in os.listdir("/dev"):
                        if entry.startswith("tty.") or entry.startswith("cu."):
                            dev_path = f"/dev/{entry}"
                            ports.append({
                                "device": dev_path,
                                "name": entry,
                                "description": entry,
                                "hwid": ""
                            })
            
            # 按设备名排序
            ports.sort(key=lambda x: x["device"])
            
        except Exception as e:
            logger.error(f"枚举串口失败: {e}")
        
        return ports
    
    async def _get_serial_config(self, params: dict) -> dict:
        """获取串口配置"""
        config = config_manager.config.serial
        
        is_open = False
        debug_hex = False
        try:
            from ..serial_port.serial_manager import serial_manager
            is_open = serial_manager._helper.is_open if serial_manager._helper else False
            debug_hex = serial_manager.get_debug_hex()
        except Exception:
            pass
        
        return {
            "enabled": config.enabled,
            "port": config.port,
            "baudrate": config.baudrate,
            "poll_interval": config.poll_interval,
            "is_open": is_open,
            "debug_hex": debug_hex
        }
    
    async def _update_serial_config(self, params: dict) -> dict:
        """更新串口配置"""
        config = config_manager.config.serial
        
        if "enabled" in params:
            config.enabled = params["enabled"]
        if "port" in params:
            config.port = params["port"]
        if "baudrate" in params:
            config.baudrate = params["baudrate"]
        if "poll_interval" in params:
            config.poll_interval = params["poll_interval"]
        
        config_manager.save()
        
        # 更新串口管理器
        try:
            from ..serial_port.serial_manager import serial_manager
            serial_manager.update_serial_config(
                enabled=params.get("enabled"),
                port=params.get("port"),
                baudrate=params.get("baudrate"),
                poll_interval=params.get("poll_interval")
            )
        except Exception as e:
            return {"message": f"配置已保存，但串口更新失败: {e}"}
        
        return {"message": "串口配置已更新"}
    
    async def _get_currents(self, params: dict) -> dict:
        """获取所有分区电流值"""
        try:
            from ..serial_port.serial_manager import serial_manager
            currents = serial_manager.get_all_currents()
            return {"currents": currents}
        except Exception as e:
            return {"currents": {}, "error": str(e)}
    
    async def _get_lora_config(self, params: dict) -> dict:
        """获取LoRa配置"""
        try:
            from ..serial_port.serial_manager import serial_manager
            return serial_manager.get_lora_config()
        except Exception as e:
            return {"id": 0, "channel": 0, "error": str(e)}
    
    async def _set_lora_config(self, params: dict) -> dict:
        """设置LoRa配置（编号和信道）"""
        lora_id = params.get("id")
        channel = params.get("channel")
        
        if lora_id is None and channel is None:
            raise ValueError("缺少 id 或 channel 参数")
        
        try:
            from ..serial_port.serial_manager import serial_manager
            messages = []
            
            # 设置编号
            if lora_id is not None:
                if serial_manager.set_lora_id(int(lora_id)):
                    messages.append(f"编号已设置为 {lora_id}")
                else:
                    raise ValueError("设置编号失败，串口可能未连接")
            
            # 设置信道
            if channel is not None:
                if serial_manager.set_lora_channel(int(channel)):
                    messages.append(f"信道已设置为 {channel}")
                else:
                    raise ValueError("设置信道失败，串口可能未连接")
            
            return {"message": "LoRa配置已更新: " + ", ".join(messages)}
        except Exception as e:
            raise ValueError(f"设置LoRa配置失败: {e}")
    
    async def _set_serial_debug(self, params: dict) -> dict:
        """
        设置串口16进制调试日志开关
        
        Args:
            params: {"enabled": bool} - 开启或关闭调试日志
            
        Returns:
            {"enabled": bool, "message": str}
        """
        enabled = params.get("enabled", False)
        
        try:
            from ..serial_port.serial_manager import serial_manager
            success = serial_manager.set_debug_hex(enabled)
            
            if success:
                status = "开启" if enabled else "关闭"
                return {
                    "enabled": enabled,
                    "message": f"串口16进制调试日志已{status}"
                }
            else:
                raise ValueError("串口未初始化")
        except Exception as e:
            raise ValueError(f"设置调试日志失败: {e}")
    
    # ==================== 巡检处理器 ====================
    
    async def _start_patrol(self, params: dict) -> dict:
        """开始巡检模式"""
        from ..patrol.patrol_manager import patrol_manager
        return patrol_manager.start_patrol()
    
    async def _stop_patrol(self, params: dict) -> dict:
        """退出巡检模式"""
        from ..patrol.patrol_manager import patrol_manager
        return patrol_manager.stop_patrol()
    
    async def _patrol_self_check(self, params: dict) -> dict:
        """设备自检"""
        from ..patrol.patrol_manager import patrol_manager
        return patrol_manager.device_self_check()
    
    async def _patrol_alarm_demo(self, params: dict) -> dict:
        """报警演示（支持单灶台或自动选择）"""
        from ..patrol.patrol_manager import patrol_manager
        
        zone_id = params.get("zone_id")
        if zone_id:
            # 单灶台报警演示
            return patrol_manager.alarm_demo_zone(zone_id)
        else:
            # 兼容旧接口，自动选择第一个动火灶台
            return patrol_manager.alarm_demo()
    
    async def _patrol_force_warning(self, params: dict) -> dict:
        """强制预警（所有区）"""
        from ..patrol.patrol_manager import patrol_manager
        return patrol_manager.force_warning_all()
    
    async def _patrol_force_alarm(self, params: dict) -> dict:
        """强制报警（所有区）"""
        from ..patrol.patrol_manager import patrol_manager
        return patrol_manager.force_alarm_all()
    
    async def _patrol_force_cutoff(self, params: dict) -> dict:
        """强制切电（所有区）"""
        from ..patrol.patrol_manager import patrol_manager
        return patrol_manager.force_cutoff_all()
    
    async def _get_patrol_status(self, params: dict) -> dict:
        """获取巡检状态"""
        from ..patrol.patrol_manager import patrol_manager
        return patrol_manager.get_state()
    
    async def _patrol_check_person(self, params: dict) -> dict:
        """检测单个灶台的离人状态"""
        zone_id = params.get("zone_id")
        if not zone_id:
            raise ValueError("缺少 zone_id 参数")
        
        from ..patrol.patrol_manager import patrol_manager
        return patrol_manager.check_person_zone(zone_id)
    
    async def _patrol_check_fire(self, params: dict) -> dict:
        """检测单个灶台的动火状态"""
        zone_id = params.get("zone_id")
        if not zone_id:
            raise ValueError("缺少 zone_id 参数")
        
        from ..patrol.patrol_manager import patrol_manager
        return patrol_manager.check_fire_zone(zone_id)
    
    async def _patrol_cutoff_zone(self, params: dict) -> dict:
        """对单个灶台执行切电"""
        zone_id = params.get("zone_id")
        if not zone_id:
            raise ValueError("缺少 zone_id 参数")
        
        from ..patrol.patrol_manager import patrol_manager
        return patrol_manager.cutoff_zone(zone_id)


    async def _trigger_update(self, params: dict) -> dict:
        """
        触发系统强制更新（全自动部署）

        使用 git fetch + reset --hard 无条件更新到远程最新版本；随后在后台依次：
        1) pip install -r requirements.txt（含蓝牙 bless 依赖）
        2) sudo bash deploy/bootstrap-ble.sh（bluez 检测兜底 + 注册/更新 ai-dhlr-ble 服务 + enable）
        3) 重启 ai-dhlr 与 ai-dhlr-ble 两个服务

        BLE 部署失败不阻断主服务（火灾监测）重启。响应立即返回，部署在后台进行，
        服务重启期间 WebSocket 会断开。

        Returns:
            {"message": str, "success": bool}
        """
        import subprocess
        import asyncio
        from pathlib import Path

        # 获取项目根目录
        project_root = Path(__file__).parent.parent.parent

        logger.info(f"触发系统强制更新: {project_root}")

        try:
            # 获取当前分支名
            result = subprocess.run(
                ["git", "rev-parse", "--abbrev-ref", "HEAD"],
                cwd=project_root,
                capture_output=True,
                text=True
            )
            branch = result.stdout.strip() or "main"

            # 强制更新代码
            subprocess.run(["git", "fetch", "origin"], cwd=project_root, check=True)
            subprocess.run(["git", "reset", "--hard", f"origin/{branch}"], cwd=project_root, check=True)

            logger.info(f"代码已更新到 origin/{branch}，后台开始部署 BLE 并重启服务...")
        except subprocess.CalledProcessError as e:
            logger.error(f"更新失败: {e}")
            raise ValueError(f"更新失败: {e.stderr}")
        except Exception as e:
            logger.error(f"更新失败: {e}")
            raise ValueError(f"更新失败: {e}")

        # 后台部署 + 重启：不 await。服务重启会断开 WebSocket，响应必须先返回给客户端。
        asyncio.create_task(self._deploy_and_restart(project_root))

        return {
            "success": True,
            "message": f"已更新到 origin/{branch}，正在后台部署蓝牙并重启服务（约1-3分钟），连接即将断开。"
        }

    async def _deploy_and_restart(self, project_root) -> None:
        """
        更新代码后的后台部署任务（不阻断、失败可见）。

        顺序：pip 依赖 → bootstrap-ble.sh（bluez + 服务注册 + enable）
              → bootstrap-hotspot.sh（热点脚本安装 + 清理 Hotspot-N 残留）→ 重启两服务。
        各步相互隔离：BLE 相关失败仅记日志，绝不阻止主服务 ai-dhlr 重启
        （火灾监测可用性优先）。全部输出追加到 logs/bootstrap-ble.log，
        便于远程排查零干预升级结果。
        """
        import asyncio
        import subprocess
        import sys
        from datetime import datetime

        log_path = project_root / "logs" / "bootstrap-ble.log"
        log_path.parent.mkdir(parents=True, exist_ok=True)

        def _log(line: str) -> None:
            logger.info(line)
            try:
                with open(log_path, "a", encoding="utf-8") as f:
                    f.write(line + "\n")
            except Exception:
                pass

        _log(f"\n======== 部署开始 {datetime.now():%F %T} ========")

        # 1) Python 依赖（含 bless）——失败仅告警，不阻断
        try:
            _log("[1/4] pip install -r requirements.txt ...")
            proc = await asyncio.create_subprocess_exec(
                sys.executable, "-m", "pip", "install", "-r", str(project_root / "requirements.txt"),
                cwd=project_root,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
            )
            try:
                stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=300.0)
            except asyncio.TimeoutError:
                proc.kill()
                _log("[WARN] pip install 超时（>300s），跳过依赖更新")
            else:
                out = stdout.decode("utf-8", errors="replace") if stdout else ""
                if proc.returncode == 0:
                    _log("[OK]   pip install 完成")
                else:
                    _log(f"[WARN] pip install 失败 rc={proc.returncode}:\n{out[-800:]}")
        except Exception as e:
            _log(f"[WARN] pip install 异常: {e}")

        # 2) bootstrap-ble.sh（bluez 检测 + 服务注册 + enable）——失败仅告警，不阻断
        try:
            _log("[2/4] deploy/bootstrap-ble.sh ...")
            proc = await asyncio.create_subprocess_exec(
                "sudo", "-S", "bash", str(project_root / "deploy" / "bootstrap-ble.sh"),
                cwd=project_root,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
            )
            try:
                stdout, _ = await asyncio.wait_for(
                    proc.communicate(input=self._SUDO_PASSWORD.encode()), timeout=240.0
                )
            except asyncio.TimeoutError:
                proc.kill()
                _log("[WARN] bootstrap-ble.sh 超时（>240s）")
            else:
                out = stdout.decode("utf-8", errors="replace") if stdout else ""
                _log(f"[INFO ] bootstrap-ble.sh rc={proc.returncode}:\n{out[-1500:]}")
        except Exception as e:
            _log(f"[WARN] bootstrap-ble.sh 异常: {e}")

        # 3) bootstrap-hotspot.sh（热点脚本安装 + 清理 Hotspot-N 残留）——失败仅告警，不阻断
        try:
            _log("[3/4] deploy/bootstrap-hotspot.sh ...")
            proc = await asyncio.create_subprocess_exec(
                "sudo", "-S", "bash", str(project_root / "deploy" / "bootstrap-hotspot.sh"),
                cwd=project_root,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
            )
            try:
                stdout, _ = await asyncio.wait_for(
                    proc.communicate(input=self._SUDO_PASSWORD.encode()), timeout=120.0
                )
            except asyncio.TimeoutError:
                proc.kill()
                _log("[WARN] bootstrap-hotspot.sh 超时（>120s）")
            else:
                out = stdout.decode("utf-8", errors="replace") if stdout else ""
                _log(f"[INFO ] bootstrap-hotspot.sh rc={proc.returncode}:\n{out[-1500:]}")
        except Exception as e:
            _log(f"[WARN] bootstrap-hotspot.sh 异常: {e}")

        # 3.5) log-hygiene.sh（logrotate 定时 + journald 上限 + rsyslog 垃圾过滤，幂等）
        #      与 update.sh 第 5 步保持一致，防止设备运行一年日志撑满根分区
        try:
            _log("[3.5/4] deploy/log-hygiene.sh ...")
            proc = await asyncio.create_subprocess_exec(
                "sudo", "-S", "bash", str(project_root / "deploy" / "log-hygiene.sh"),
                cwd=project_root,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
            )
            try:
                stdout, _ = await asyncio.wait_for(
                    proc.communicate(input=self._SUDO_PASSWORD.encode()), timeout=120.0
                )
            except asyncio.TimeoutError:
                proc.kill()
                _log("[WARN] log-hygiene.sh 超时（>120s）")
            else:
                out = stdout.decode("utf-8", errors="replace") if stdout else ""
                _log(f"[INFO ] log-hygiene.sh rc={proc.returncode}:\n{out[-800:]}")
        except Exception as e:
            _log(f"[WARN] log-hygiene.sh 异常: {e}")

        # 3.6) time-sync.sh（chrony/ntpdate 时间同步，镜像时间服务全坏）
        #      与 update.sh 第 6 步保持一致；时钟步进约 1s 内完成，不重启服务
        try:
            _log("[3.6/4] deploy/time-sync.sh ...")
            proc = await asyncio.create_subprocess_exec(
                "sudo", "-S", "bash", str(project_root / "deploy" / "time-sync.sh"),
                cwd=project_root,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
            )
            try:
                stdout, _ = await asyncio.wait_for(
                    proc.communicate(input=self._SUDO_PASSWORD.encode()), timeout=180.0
                )
            except asyncio.TimeoutError:
                proc.kill()
                _log("[WARN] time-sync.sh 超时（>180s）")
            else:
                out = stdout.decode("utf-8", errors="replace") if stdout else ""
                _log(f"[INFO ] time-sync.sh rc={proc.returncode}:\n{out[-600:]}")
        except Exception as e:
            _log(f"[WARN] time-sync.sh 异常: {e}")

        # 同步主服务配置（与 update.sh 一致）：deploy/ai-dhlr.service → /etc/systemd/system/
        # 用于随 git 下发 systemd 配置变更（如 MALLOC_ARENA_MAX=2）。
        # 不一致才 cp + daemon-reload，幂等；必须在 restart 之前，否则新进程仍用旧 service。
        # 旧设备多经前端“立即更新系统”按钮升级，不同步则 service 变更永远到不了设备。
        from pathlib import Path
        deploy_service = project_root / "deploy" / "ai-dhlr.service"
        etc_service = Path("/etc/systemd/system/ai-dhlr.service")
        try:
            if deploy_service.exists():
                deploy_bytes = deploy_service.read_bytes()
                etc_bytes = etc_service.read_bytes() if etc_service.exists() else b""
                if deploy_bytes != etc_bytes:
                    for cmd in (
                        ["sudo", "-S", "cp", str(deploy_service), str(etc_service)],
                        ["sudo", "-S", "systemctl", "daemon-reload"],
                    ):
                        subprocess.run(
                            cmd,
                            input=self._SUDO_PASSWORD.encode(),
                            capture_output=True,
                            timeout=30,
                        )
                    _log("[OK]   主服务配置已同步（cp + daemon-reload）")
        except Exception as e:
            _log(f"[WARN] 主服务配置同步失败（不阻断重启）: {e}")

        # 4) 重启服务——主服务必然重启（放最后）；BLE 已 enable 则起来，未就绪由其 Restart=always 自处理
        _log("[4/4] 重启服务 ...")
        for unit in ("ai-dhlr", "ai-dhlr-ble"):
            try:
                subprocess.run(
                    ["sudo", "-S", "systemctl", "restart", unit],
                    input=self._SUDO_PASSWORD.encode(),
                    capture_output=True,
                    timeout=30,
                )
                _log(f"[OK]   systemctl restart {unit}")
            except Exception as e:
                level = "WARN" if unit == "ai-dhlr-ble" else "FAIL"
                _log(f"[{level}] restart {unit} 失败: {e}")

        _log(f"======== 部署结束 {datetime.now():%F %T} ========")

    async def _install_dependencies(self, params: dict) -> dict:
        """
        安装/更新 Python 依赖包

        执行 pip install -r requirements.txt 命令。
        使用异步子进程，不阻塞主线程。

        Returns:
            {"success": bool, "message": str, "output": str}
        """
        import asyncio
        import sys
        from pathlib import Path

        # 获取项目根目录
        project_root = Path(__file__).parent.parent.parent
        requirements_path = project_root / "requirements.txt"

        if not requirements_path.exists():
            raise ValueError("requirements.txt 文件不存在")

        logger.info(f"开始安装依赖: {requirements_path}")

        try:
            # 使用 asyncio 创建子进程执行 pip install
            # 使用 sys.executable 确保使用当前 Python 解释器
            process = await asyncio.create_subprocess_exec(
                sys.executable, "-m", "pip", "install", "-r", str(requirements_path),
                cwd=project_root,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT
            )

            # 等待完成，设置超时（5分钟）
            try:
                stdout, _ = await asyncio.wait_for(
                    process.communicate(),
                    timeout=300.0
                )
            except asyncio.TimeoutError:
                process.kill()
                raise ValueError("安装超时（超过5分钟）")

            output = stdout.decode('utf-8', errors='replace')

            if process.returncode == 0:
                logger.info("依赖安装成功")
                return {
                    "success": True,
                    "message": "依赖安装成功",
                    "output": output[-2000:] if len(output) > 2000 else output
                }
            else:
                logger.error(f"依赖安装失败: returncode={process.returncode}")
                raise ValueError(f"安装失败 (exit code {process.returncode}):\n{output[-1000:]}")

        except Exception as e:
            logger.error(f"安装依赖时发生错误: {e}")
            raise ValueError(f"安装失败: {str(e)}")

    # ==================== GPIO 处理器 ====================
    
    async def _get_gpio_pins(self, params: dict) -> dict:
        """获取可用的 GPIO 引脚列表"""
        from ..output.gpio import list_gpio_pins
        gpio_path = config_manager.config.gpio.gpio_path
        pins = list_gpio_pins(gpio_path)
        return {"pins": pins}
    
    async def _get_gpio_config(self, params: dict) -> dict:
        """获取 GPIO 配置"""
        gpio = config_manager.config.gpio
        return {
            "enabled": gpio.enabled,
            "gpio_path": gpio.gpio_path,
            "pin_fire": gpio.pin_fire,
            "pin_absence": gpio.pin_absence,
            "pin_alarm": gpio.pin_alarm,
            "estop_enabled": gpio.estop_enabled,
            "pin_estop": gpio.pin_estop,
            "estop_active_low": gpio.estop_active_low
        }
    
    async def _update_gpio_config(self, params: dict) -> dict:
        """更新 GPIO 配置"""
        gpio = config_manager.config.gpio
        
        if "enabled" in params:
            gpio.enabled = params["enabled"]
        if "pin_fire" in params:
            gpio.pin_fire = params["pin_fire"]
        if "pin_absence" in params:
            gpio.pin_absence = params["pin_absence"]
        if "pin_alarm" in params:
            gpio.pin_alarm = params["pin_alarm"]
        if "estop_enabled" in params:
            gpio.estop_enabled = params["estop_enabled"]
        if "pin_estop" in params:
            gpio.pin_estop = params["pin_estop"]
        if "estop_active_low" in params:
            gpio.estop_active_low = params["estop_active_low"]
        
        config_manager.save()
        
        # 重新加载指示灯控制器配置
        try:
            from ..output.gpio import get_indicator_controller
            controller = get_indicator_controller()
            if controller:
                controller.reload_config(gpio)
                logger.info("已重新加载 GPIO 指示灯配置")
        except Exception as e:
            logger.warning(f"重新加载 GPIO 指示灯配置失败: {e}")

        # 重新加载急停监听器配置
        try:
            from ..output.gpio import reload_estop_monitor
            reload_estop_monitor(gpio)
            logger.info("已重新加载急停监听器配置")
        except Exception as e:
            logger.warning(f"重新加载急停监听器配置失败: {e}")
        
        return {"message": "GPIO 配置已更新"}

    # ==================== USB OTG 模式处理器 ====================

    _OTG_MODE_PATH = "/sys/devices/platform/fe8a0000.usb2-phy/otg_mode"
    _SUDO_PASSWORD = "linaro"

    async def _get_usb_otg_mode(self, params: dict) -> dict:
        """获取当前 USB OTG 模式"""
        import subprocess
        try:
            result = subprocess.run(
                ["sudo", "-S", "cat", self._OTG_MODE_PATH],
                input=self._SUDO_PASSWORD.encode(),
                capture_output=True,
                timeout=5,
            )
            if result.returncode != 0:
                stderr = result.stderr.decode().strip()
                if "No such file" in stderr:
                    raise ValueError("当前设备不支持 USB OTG 模式切换")
                raise ValueError(f"读取失败: {stderr}")
            return {"mode": result.stdout.decode().strip()}
        except FileNotFoundError:
            raise ValueError("当前设备不支持 sudo 命令")
        except ValueError:
            raise
        except Exception as e:
            logger.error(f"读取 USB OTG 模式失败: {e}")
            raise ValueError(f"读取 USB OTG 模式失败: {e}")

    async def _set_usb_otg_mode(self, params: dict) -> dict:
        """设置 USB OTG 模式"""
        import subprocess
        mode = params.get("mode")
        if mode not in ("host", "peripheral"):
            raise ValueError("无效的 USB OTG 模式，仅支持 'host' 或 'peripheral'")

        try:
            result = subprocess.run(
                ["sudo", "-S", "sh", "-c", f"echo {mode} > {self._OTG_MODE_PATH}"],
                input=self._SUDO_PASSWORD.encode(),
                capture_output=True,
                timeout=5,
            )
            if result.returncode != 0:
                stderr = result.stderr.decode().strip()
                if "No such file" in stderr:
                    raise ValueError("当前设备不支持 USB OTG 模式切换")
                raise ValueError(f"设置失败: {stderr}")
            logger.info(f"USB OTG 模式已切换为: {mode}")
            return {"mode": mode, "message": f"已切换为 {mode} 模式"}
        except FileNotFoundError:
            raise ValueError("当前设备不支持 sudo 命令")
        except ValueError:
            raise
        except Exception as e:
            logger.error(f"设置 USB OTG 模式失败: {e}")
            raise ValueError(f"设置 USB OTG 模式失败: {e}")

    # ==================== 开机自启热点处理器 ====================

    async def _get_hotspot_autostart(self, params: dict) -> dict:
        """获取开机自启热点状态。

        返回 ``{supported, enabled, active, config_enabled}``：
        - supported: 设备是否支持（Linux 且安装了 hotspot-startup.service）。
        - enabled:   systemd is-enabled 实际状态。
        - active:    wlan0 当前是否正处 AP（热点在跑）。
        - config_enabled: config.yaml 中的配置值。
        """
        from ..utils.hotspot import get_state
        try:
            state = get_state()
        except FileNotFoundError:
            state = {"supported": False, "enabled": False, "active": False}
        except Exception as e:
            logger.error(f"读取开机自启热点状态失败: {e}")
            raise ValueError(f"读取开机自启热点状态失败: {e}")
        state["config_enabled"] = config_manager.config.hotspot.auto_start_on_boot
        return state

    async def _set_hotspot_autostart(self, params: dict) -> dict:
        """开关开机自启热点。

        - 开启：systemctl enable（仅下次开机生效，不动当前网络）。
        - 关闭：systemctl disable --now + 立即停掉当前活动热点。
        结果同步写回 config.yaml。
        """
        from ..utils.hotspot import apply_autostart
        enabled = params.get("enabled")
        if enabled is None or not isinstance(enabled, bool):
            raise ValueError("缺少或非法的 enabled 参数（需 true/false）")

        try:
            result = apply_autostart(enabled)
        except FileNotFoundError:
            raise ValueError("当前设备不支持该操作（非 Linux 或无 sudo）")
        except Exception as e:
            logger.error(f"设置开机自启热点失败: {e}")
            raise ValueError(f"设置开机自启热点失败: {e}")

        if not result.get("supported"):
            raise ValueError(result.get("error", "当前设备不支持开机自启热点控制"))
        if not result.get("ok"):
            raise ValueError(result.get("error", "操作失败"))

        # 持久化到配置，保证启动同步与展示一致
        config_manager.config.hotspot.auto_start_on_boot = enabled
        config_manager.save()
        logger.info(f"开机自启热点已{'启用' if enabled else '关闭'}")

        # 回读真实状态（active 反映当前热点是否在跑）
        from ..utils.hotspot import get_state as _get_state
        fresh = _get_state()
        return {
            "supported": True,
            "enabled": fresh.get("enabled", enabled),
            "active": fresh.get("active", False),
            "config_enabled": enabled,
            "message": result.get("message", "设置已更新"),
        }


# 全局处理器实例
ws_handler = WSHandler()