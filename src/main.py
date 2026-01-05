"""
动火离人安全监测系统 - 主程序入口
"""
import os
import sys
import signal
import threading
import time
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
from src.output.gpio import gpio_controller
from src.api.server import create_app
from src.api.websocket import sync_broadcast_state_change


class FireSafetySystem:
    """动火离人安全监测系统主类"""
    
    def __init__(self, config_path: str = None):
        self._config_path = config_path
        self._running = False
        self._detection_thread = None
        self._detector: PersonDetector = None
        self._logger = None
    
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
                on_cutoff=self._on_cutoff,
                on_state_change=self._on_state_change
            )
            
            # 为每个灶台注册检测状态
            for zone_config in config.zones:
                self._detector.register_zone(zone_config)
            
            # 初始化语音播报
            voice_player.initialize(
                enabled=config.voice.enabled,
                engine=config.voice.engine,
                rate=config.voice.rate,
                volume=config.voice.volume
            )
            
            # 初始化GPIO控制
            gpio_controller.initialize(simulated=config.gpio.simulated)
            
            # 启动性能监控
            try:
                from src.utils.performance import performance_monitor
                performance_monitor.start()
            except ImportError:
                self._logger.warning("性能监控模块不可用")
            
            self._logger.info("系统初始化完成")
            return True
            
        except Exception as e:
            if self._logger:
                self._logger.error(f"系统初始化失败: {e}")
            else:
                print(f"系统初始化失败: {e}")
            return False
    
    def _on_warning(self, zone: Zone):
        """预警回调"""
        timeout = get_config().safety.warning_timeout
        self._logger.warning(f"[预警] {zone.name} 无人看管超过 {timeout} 秒")
        voice_player.speak_warning(zone.name)
    
    def _on_cutoff(self, zone: Zone):
        """切电回调"""
        timeout = get_config().safety.cutoff_timeout
        self._logger.warning(f"[切电] {zone.name} 无人看管超过 {timeout} 秒，执行切电")
        voice_player.speak_cutoff(zone.name)
        gpio_controller.cutoff(zone.id)
    
    def _on_state_change(self, event: StateChangeEvent):
        """状态变化回调"""
        self._logger.info(f"[状态变化] {event.zone_name}: {event.old_state.value} -> {event.new_state.value}")
        
        # 广播状态变化到WebSocket
        sync_broadcast_state_change({
            "zone_id": event.zone_id,
            "zone_name": event.zone_name,
            "old_state": event.old_state.value,
            "new_state": event.new_state.value,
            "timestamp": event.timestamp,
            "message": event.message
        })
    
    def _detection_loop(self):
        """检测循环"""
        self._logger.info("检测循环已启动")
        
        while self._running:
            try:
                # 遍历所有灶台
                for sm in zone_manager.get_all_zones():
                    if not sm.zone.enabled:
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
                    
                    # 更新状态机
                    zone_manager.update_zone(sm.zone.id, has_person, frame)
                
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
        
        # 停止各组件
        voice_player.stop()
        camera_manager.stop_all()
        if self._detector:
            self._detector.release()
        gpio_controller.cleanup()
        
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
            log_level="info"
        )


# 全局系统实例
_system: FireSafetySystem = None


def get_zone_callbacks():
    """获取灶台回调函数（供API使用）"""
    if _system:
        return {
            "on_warning": _system._on_warning,
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
