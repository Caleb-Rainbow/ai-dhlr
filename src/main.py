"""
动火离人安全监测系统 - 主程序入口
"""
import os
import sys
import signal
import threading
import time
import base64
from pathlib import Path
import cv2

# 设置OpenCV日志级别为错误级，屏蔽枚举设备时的警告
os.environ['OPENCV_LOG_LEVEL'] = 'ERROR'

# 添加项目根目录到路径
sys.path.insert(0, str(Path(__file__).parent.parent))

import uvicorn

from src.utils.config import config_manager, get_config
from src.utils.logger import event_logger, get_logger
from src.camera.manager import camera_manager
from src.detection.detector import PersonDetector
from src.zone.state_machine import zone_manager, ZoneState, StateChangeEvent
from src.zone.models import Zone
from src.output.voice import voice_player
from src.api.server import create_app
from src.api.websocket import sync_broadcast_state_change, sync_broadcast_alarm_event
from src.patrol.patrol_manager import patrol_manager


class FireSafetySystem:
    """动火离人安全监测系统主类"""
    
    def __init__(self, config_path: str = None):
        self._config_path = config_path
        self._running = False
        self._detection_thread = None
        self._detector: PersonDetector = None
        self._logger = None
        
        # 语音循环播报控制
        self._broadcast_threads: dict = {}  # zone_id -> Thread
        self._broadcast_stop_flags: dict = {}  # zone_id -> bool (停止标志)
        self._broadcast_lock = threading.Lock()

    
    def initialize(self) -> bool:
        """初始化系统"""
        try:
            # 加载配置
            config = config_manager.load(self._config_path)
            
            # 初始化日志
            event_logger.setup(
                level=config.logging.level,
                log_dir=config.logging.log_dir,
                snapshot_dir=config.logging.snapshot_dir
            )
            self._logger = get_logger()
            self._logger.info("=" * 50)
            self._logger.info(f"  {config.system.name} v{config.system.version}")
            self._logger.info("=" * 50)
            
            # 初始化摄像头
            camera_manager.initialize_from_config(config.cameras)
            camera_manager.start_all()
            
            # 初始化人形检测器
            self._detector = PersonDetector(config.inference, config.detection)
            if not self._detector.initialize():
                self._logger.error("人形检测器初始化失败")
                return False
            
            # 初始化灶台状态机
            zone_manager.initialize_from_config(
                config.zones,
                on_warning=self._on_warning,
                on_alarm=self._on_alarm,
                on_cutoff=self._on_cutoff,
                on_state_change=self._on_state_change
            )
            
            # 为每个灶台注册检测状态
            for zone_config in config.zones:
                self._detector.register_zone(zone_config)
            
            # 初始化语音播报
            voice_player.initialize(
                enabled=config.voice.enabled,
                volume=config.voice.volume
            )
            # 初始化TTS管理器（Kokoro智能生命周期管理）
            if config.tts.enabled:
                try:
                    from src.tts.tts_manager import tts_manager
                    tts_manager.initialize(
                        audio_dir=config.tts.audio_dir,
                        idle_timeout=config.tts.idle_timeout,
                        warning_message=config.alarm.warning_message,
                        alarm_message=config.alarm.alarm_message,
                        action_message=config.alarm.action_message
                    )
                    
                    # 检查已配置灶台的语音文件，如缺失则提交合成任务
                    for zone_config in config.zones:
                        if not tts_manager.has_audio_files(zone_config.id):
                            tts_manager.submit_synthesis_task(zone_config.id, zone_config.name)
                    
                    self._logger.info("TTS管理器初始化完成")
                except Exception as e:
                    self._logger.warning(f"TTS管理器初始化失败: {e}")
            
            # 生成或获取设备ID
            try:
                from src.utils.device_id import get_or_create_device_id
                device_id = get_or_create_device_id(config_manager)
                self._logger.info(f"设备ID: {device_id}")
            except Exception as e:
                self._logger.warning(f"设备ID生成失败: {e}")
            
            # 启动性能监控
            try:
                from src.utils.performance import performance_monitor
                performance_monitor.start()
            except ImportError:
                self._logger.warning("性能监控模块不可用")
            
            # 初始化串口管理器
            try:
                from src.serial_port.serial_manager import serial_manager
                serial_manager.initialize(
                    enabled=config.serial.enabled,
                    port=config.serial.port,
                    baudrate=config.serial.baudrate,
                    poll_interval=config.serial.poll_interval
                )
                
                # 注册所有灶台到串口管理器
                for zone_config in config.zones:
                    serial_manager.register_zone(
                        zone_config.id,
                        zone_config.serial_index,
                        zone_config.fire_current_threshold
                    )
                
                self._logger.info("串口管理器初始化完成")
            except Exception as e:
                self._logger.warning(f"串口管理器初始化失败: {e}")
            
            # 初始化 GPIO 指示灯控制器
            try:
                from src.output.gpio import init_indicator_controller
                self._indicator_controller = init_indicator_controller(config.gpio)
                if self._indicator_controller.is_available():
                    self._logger.info("GPIO 指示灯控制器初始化完成")
                else:
                    self._logger.info("GPIO 指示灯控制器已初始化（sysfs 不可用，已禁用）")
            except Exception as e:
                self._logger.warning(f"GPIO 指示灯控制器初始化失败: {e}")
                self._indicator_controller = None
            
            self._logger.info("系统初始化完成")
            return True
            
        except Exception as e:
            if self._logger:
                self._logger.error(f"系统初始化失败: {e}")
            else:
                print(f"系统初始化失败: {e}")
            return False
    
    def _on_warning(self, zone: Zone, frame = None):
        """预警回调（第一阶段）- 加入播报队列"""
        config = get_config()
        self._logger.warning(f"[预警] {zone.name} 无人看管超过 {config.alarm.warning_time} 秒")
        self._add_to_broadcast_queue(zone.id, zone.name, "warning")
        
        image_base64 = self._frame_to_base64(frame) if frame is not None else None
        sync_broadcast_alarm_event(zone.id, zone.name, "warning", image_base64)
    
    def _on_alarm(self, zone: Zone, frame = None):
        """报警回调（第二阶段）- 更新播报内容"""
        config = get_config()
        self._logger.warning(f"[报警] {zone.name} 无人看管超过 {config.alarm.alarm_time} 秒")
        self._add_to_broadcast_queue(zone.id, zone.name, "alarm")
        
        image_base64 = self._frame_to_base64(frame) if frame is not None else None
        sync_broadcast_alarm_event(zone.id, zone.name, "alarm", image_base64)
    
    def _on_cutoff(self, zone: Zone, frame = None):
        """切电回调（第三阶段）- 执行切电并更新播报内容"""
        config = get_config()
        self._logger.warning(f"[切电] {zone.name} 无人看管超过 {config.alarm.action_time} 秒，执行切电")
        
        # 使用串口管理器执行切电（实际硬件）
        try:
            from src.serial_port.serial_manager import serial_manager
            serial_manager.cutoff(zone.id)
        except Exception as e:
            self._logger.warning(f"串口切电失败: {e}")
        
        self._add_to_broadcast_queue(zone.id, zone.name, "action")
        
        image_base64 = self._frame_to_base64(frame) if frame is not None else None
        sync_broadcast_alarm_event(zone.id, zone.name, "cutoff", image_base64)
    
    def _on_state_change(self, event: StateChangeEvent):
        """状态变化回调 - 根据状态决定是否停止播报"""
        self._logger.info(f"[状态变化] {event.zone_name}: {event.old_state.value} -> {event.new_state.value}")
        
        # 判断是否需要停止播报
        # 停止条件: 变为空闲或有人看管状态
        stop_states = [ZoneState.IDLE, ZoneState.ACTIVE_WITH_PERSON]
        if event.new_state in stop_states:
            self._remove_from_broadcast_queue(event.zone_id)
        
        # 广播状态变化到WebSocket
        sync_broadcast_state_change({
            "zone_id": event.zone_id,
            "zone_name": event.zone_name,
            "old_state": event.old_state.value,
            "new_state": event.new_state.value,
            "timestamp": event.timestamp,
            "message": event.message
        })
    
    def _add_to_broadcast_queue(self, zone_id: str, zone_name: str, audio_type: str):
        """添加或更新灶台到播报队列"""
        with self._broadcast_lock:
            self._broadcast_stop_flags[zone_id] = {
                "zone_name": zone_name,
                "audio_type": audio_type,
                "active": True
            }
            self._logger.info(f"[{zone_id}] 加入播报队列: {audio_type}")
            
            # 确保播报管理线程在运行
            self._ensure_broadcast_manager_running()
    
    def _remove_from_broadcast_queue(self, zone_id: str):
        """从播报队列移除灶台"""
        with self._broadcast_lock:
            if zone_id in self._broadcast_stop_flags:
                info = self._broadcast_stop_flags[zone_id]
                # 只在状态真正变化时才打印日志
                if isinstance(info, dict) and info.get("active", False):
                    info["active"] = False
                    self._logger.info(f"[{zone_id}] 已标记停止播报")
    
    def _frame_to_base64(self, frame) -> str:
        """将图像帧转换为Base64编码"""
        if frame is None:
            return None
        try:
            _, jpeg = cv2.imencode('.jpg', frame, [int(cv2.IMWRITE_JPEG_QUALITY), 80])
            return base64.b64encode(jpeg.tobytes()).decode('utf-8')
        except Exception as e:
            self._logger.error(f"图像Base64编码失败: {e}")
            return None
    
    def _ensure_broadcast_manager_running(self):
        """确保播报管理线程在运行"""
        if self._broadcast_threads.get("_manager") is None or not self._broadcast_threads["_manager"].is_alive():
            thread = threading.Thread(target=self._broadcast_manager_loop, daemon=True)
            self._broadcast_threads["_manager"] = thread
            thread.start()
            self._logger.info("播报管理器已启动")
    
    def _broadcast_manager_loop(self):
        """播报管理器主循环 - 轮询式播报所有待播报灶台"""
        self._logger.info("播报管理器开始运行")
        
        while self._running:
            try:
                # 巡检模式下暂停常规播报
                if patrol_manager.is_active:
                    time.sleep(0.5)
                    continue
                
                config = get_config()
                interval = config.alarm.broadcast_interval
                
                # 获取当前活跃的播报任务列表
                # 为了保证顺序稳定性，按zone_id排序
                with self._broadcast_lock:
                    active_zones = sorted([
                        (zone_id, info["zone_name"], info["audio_type"])
                        for zone_id, info in self._broadcast_stop_flags.items()
                        if isinstance(info, dict) and info.get("active", False)
                    ], key=lambda x: x[0])
                
                if not active_zones:
                    # 没有需要播报的灶台，等待一小段时间后继续检查
                    time.sleep(0.5)
                    continue
                
                # 轮流播报每个灶台
                for zone_id, zone_name, audio_type in active_zones:
                    # 再次检查是否仍然需要播报（可能已被移除）
                    audio_type_real = ""
                    with self._broadcast_lock:
                        info = self._broadcast_stop_flags.get(zone_id)
                        if not isinstance(info, dict) or not info.get("active", False):
                            continue
                        # 获取最新的播报类型（可能已升级）
                        audio_type_real = info.get("audio_type", audio_type)
                        zone_name = info.get("zone_name", zone_name)
                    
                    # 记录开始时间
                    cycle_start_time = time.time()

                    # 1. 执行播报
                    try:
                        self._logger.debug(f"[{zone_id}] 播报 {audio_type_real}")
                        if audio_type_real == "warning":
                            voice_player.speak_warning(zone_id, zone_name)
                        elif audio_type_real == "alarm":
                            voice_player.speak_alarm(zone_id, zone_name)
                        elif audio_type_real == "action":
                            voice_player.speak_cutoff(zone_id, zone_name)
                        
                        # 短暂休眠确保任务加入队列
                        time.sleep(0.1)
                        
                    except Exception as e:
                        self._logger.error(f"[{zone_id}] 播报提交失败: {e}")
                        continue

                    # 2. 等待播放结束 (忙碌等待)
                    while self._running:
                        # 检查活跃状态
                        with self._broadcast_lock:
                            info = self._broadcast_stop_flags.get(zone_id)
                            if not isinstance(info, dict) or not info.get("active", False):
                                break

                        if not voice_player.is_busy:
                            break
                        time.sleep(0.1)
                    
                    if not self._running:
                        break

                    # 3. 等待剩余间隔时间 (补齐周期)
                    # 用户的定义: 间隔 = 两个音频插入的间隔。
                    # 所以 等待时间 = 设置的间隔 - 实际播放耗时
                    elapsed = time.time() - cycle_start_time
                    wait_time = max(0.5, interval - elapsed) # 至少保留0.5秒间隔
                    
                    # self._logger.debug(f"[{zone_id}] 播放耗时 {elapsed:.2f}s, 剩余等待 {wait_time:.2f}s")
                    
                    wait_count = int(wait_time * 10)
                    for _ in range(wait_count):
                        if not self._running:
                            break
                        
                        # 在等待期间也检查活跃状态
                        with self._broadcast_lock:
                            info = self._broadcast_stop_flags.get(zone_id)
                            if not isinstance(info, dict) or not info.get("active", False):
                                break
                                
                        time.sleep(0.1)
                    
                    if not self._running:
                        break
                        
            except Exception as e:
                self._logger.error(f"播报管理器错误: {e}")
                time.sleep(1)
        
        self._logger.info("播报管理器已停止")
    
    def _cleanup_broadcast_queue(self):
        """清理播报队列中已停止的灶台"""
        with self._broadcast_lock:
            inactive_zones = [
                zone_id for zone_id, info in self._broadcast_stop_flags.items()
                if isinstance(info, dict) and not info.get("active", False)
            ]
            for zone_id in inactive_zones:
                del self._broadcast_stop_flags[zone_id]
                self._logger.debug(f"[{zone_id}] 已从播报队列移除")

    
    def _detection_loop(self):
        """检测循环"""
        self._logger.info("检测循环已启动")
        
        # 获取串口管理器引用
        serial_mgr = None
        try:
            from src.serial_port.serial_manager import serial_manager
            serial_mgr = serial_manager
        except Exception:
            pass
        
        while self._running:
            try:
                # 遍历所有灶台
                for sm in zone_manager.get_all_zones():
                    # 检查灶台是否启用
                    if not sm.zone.enabled:
                        # 灶台停用时：停止播报并强制重置状态
                        self._remove_from_broadcast_queue(sm.zone.id)
                        sm.force_idle()
                        continue
                    
                    # 获取对应摄像头的帧
                    camera = camera_manager.get_camera(sm.zone.camera_id)
                    if not camera or not camera.is_online:
                        continue
                    
                    frame = camera.get_frame()
                    if frame is None:
                        continue
                    
                    # 检测该区域是否有人
                    has_person, _ = self._detector.check_zone(
                        sm.zone.id,
                        frame,
                        sm.zone.roi
                    )
                    
                    # 从串口管理器获取动火状态（如果可用）
                    is_fire_on = False
                    if serial_mgr:
                        is_fire_on = serial_mgr.is_fire_on(sm.zone.id)
                    else:
                        is_fire_on = zone_manager._fire_states.get(sm.zone.id, False)
                    
                    # 始终更新检测结果到 zone 对象，确保巡检模式下也能获取实时状态
                    sm.zone.has_person = has_person
                    sm.zone.is_fire_on = is_fire_on
                    
                    # 仅在非巡检模式下更新状态机（触发状态转换和回调）
                    if not patrol_manager.is_active:
                        sm.update(has_person, is_fire_on, frame)
                
                # 更新 GPIO 指示灯状态
                if hasattr(self, '_indicator_controller') and self._indicator_controller:
                    try:
                        zones = zone_manager.get_all_zones()
                        enabled_zones = [z for z in zones if z.zone.enabled]
                        
                        # 聚合所有区域状态
                        has_fire = any(z.zone.is_fire_on for z in enabled_zones)
                        has_absence = any(
                            not z.zone.has_person
                            for z in enabled_zones
                        )
                        has_alarm = any(
                            z.state.value in ['warning', 'alarm', 'cutoff']
                            for z in enabled_zones
                        )
                        self._logger.debug(f"指示灯状态: has_fire={has_fire}, has_absence={has_absence}, has_alarm={has_alarm}")
                        # 更新指示灯
                        self._indicator_controller.update_indicators(has_fire, has_absence, has_alarm)
                    except Exception as e:
                        self._logger.error(f"指示灯更新错误: {e}")
                        pass  # 忽略指示灯更新错误，不影响主流程
                
                # 控制检测频率
                time.sleep(0.1)  # ~10 FPS
                
            except Exception as e:
                self._logger.error(f"检测循环错误: {e}")
                time.sleep(0.5)
        
        self._logger.info("检测循环已停止")
    
    def start(self):
        """启动系统"""
        if self._running:
            return
        
        self._running = True
        
        # 启动检测线程
        self._detection_thread = threading.Thread(target=self._detection_loop, daemon=True)
        self._detection_thread.start()
        
        self._logger.info("系统已启动")
    
    def stop(self):
        """停止系统"""
        self._running = False
        
        if self._detection_thread:
            self._detection_thread.join(timeout=2.0)
        
        # 停止所有播报（标记为非活跃）
        with self._broadcast_lock:
            for zone_id, info in self._broadcast_stop_flags.items():
                if isinstance(info, dict):
                    info["active"] = False
        
        # 停止各组件
        voice_player.stop()
        camera_manager.stop_all()
        if self._detector:
            self._detector.release()
        
        self._logger.info("系统已停止")
    
    def run_api_server(self):
        """运行API服务器"""
        config = get_config()
        app = create_app()
        
        self._logger.info(f"API服务器启动于 http://{config.api.host}:{config.api.port}")
        self._logger.info(f"Swagger文档: http://localhost:{config.api.port}/docs")
        
        uvicorn.run(
            app,
            host=config.api.host,
            port=config.api.port,
            log_level="info",
            log_config=None
        )


# 全局系统实例
_system: FireSafetySystem = None


def get_zone_callbacks():
    """获取灶台回调函数（供API使用）"""
    if _system:
        return {
            "on_warning": _system._on_warning,
            "on_alarm": _system._on_alarm,
            "on_cutoff": _system._on_cutoff,
            "on_state_change": _system._on_state_change
        }
    return {}


def main():
    """主函数"""
    global _system
    
    # 创建系统实例
    _system = FireSafetySystem()
    
    # 初始化
    if not _system.initialize():
        print("系统初始化失败，退出")
        sys.exit(1)
    
    # 信号处理
    def signal_handler(signum, frame):
        print("\n收到退出信号，正在关闭...")
        _system.stop()
        sys.exit(0)
    
    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)
    
    # 启动系统
    _system.start()
    
    # 运行API服务器（阻塞）
    try:
        _system.run_api_server()
    except KeyboardInterrupt:
        pass
    finally:
        _system.stop()


if __name__ == "__main__":
    main()
