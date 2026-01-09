"""
测试 utils/config.py 模块

测试内容:
- 配置数据类默认值
- 配置解析基本功能
"""
import pytest

from src.utils.config import (
    CameraConfig, 
    ZoneConfig, 
    AlarmConfig,
    SafetyConfig,
    TTSConfig,
    InferenceConfig,
    DetectionConfig,
    ApiConfig,
    VoiceConfig,
    LoggingConfig,
    GpioConfig,
    SystemConfig,
    SerialConfig,
    RemoteServerConfig
)


class TestCameraConfig:
    """测试 CameraConfig 数据类"""
    
    def test_default_values(self):
        """测试默认值"""
        config = CameraConfig(
            id="cam_1",
            type="usb",
            name="测试摄像头"
        )
        assert config.id == "cam_1"
        assert config.type == "usb"
        assert config.name == "测试摄像头"
        assert config.device is None
        assert config.rtsp_url is None
        assert config.width == 640
        assert config.height == 480
        assert config.fps == 30
    
    def test_usb_camera(self):
        """测试 USB 摄像头配置"""
        config = CameraConfig(
            id="usb_0",
            type="usb",
            name="USB摄像头",
            device=0
        )
        assert config.device == 0
        assert config.rtsp_url is None
    
    def test_rtsp_camera(self):
        """测试 RTSP 摄像头配置"""
        config = CameraConfig(
            id="rtsp_1",
            type="rtsp",
            name="网络摄像头",
            rtsp_url="rtsp://192.168.1.100:554/stream"
        )
        assert config.rtsp_url == "rtsp://192.168.1.100:554/stream"


class TestZoneConfig:
    """测试 ZoneConfig 数据类"""
    
    def test_default_values(self):
        """测试默认值"""
        config = ZoneConfig(
            id="zone_1",
            name="灶台1",
            camera_id="cam_1",
            roi=[(0, 0), (1, 0), (1, 1), (0, 1)]
        )
        assert config.enabled is True
        assert config.serial_index == 0
        assert config.fire_current_threshold == 100
    
    def test_custom_values(self):
        """测试自定义值"""
        config = ZoneConfig(
            id="zone_2",
            name="灶台2",
            camera_id="cam_1",
            roi=[(0.1, 0.1)],
            enabled=False,
            serial_index=2,
            fire_current_threshold=150
        )
        assert config.enabled is False
        assert config.serial_index == 2
        assert config.fire_current_threshold == 150


class TestAlarmConfig:
    """测试 AlarmConfig 数据类"""
    
    def test_default_values(self):
        """测试默认值 (三阶段)"""
        config = AlarmConfig()
        
        assert config.warning_time == 90
        assert config.alarm_time == 180
        assert config.action_time == 300
        assert config.broadcast_interval == 15
        assert "即将超时" in config.warning_message or "预警" in config.warning_message.lower() or len(config.warning_message) > 0
    
    def test_custom_times(self):
        """测试自定义时间"""
        config = AlarmConfig(
            warning_time=60,
            alarm_time=120,
            action_time=180
        )
        assert config.warning_time == 60
        assert config.alarm_time == 120
        assert config.action_time == 180


class TestSafetyConfig:
    """测试 SafetyConfig 数据类（旧版兼容）"""
    
    def test_default_values(self):
        """测试默认值"""
        config = SafetyConfig()
        assert config.warning_timeout == 30
        assert config.cutoff_timeout == 60


class TestInferenceConfig:
    """测试 InferenceConfig 数据类"""
    
    def test_default_values(self):
        """测试默认值"""
        config = InferenceConfig()
        assert config.engine == "pytorch"
        assert config.model_path == "yolo11n.pt"
        assert config.confidence_threshold == 0.5
        assert config.person_class_id == 0


class TestDetectionConfig:
    """测试 DetectionConfig 数据类"""
    
    def test_default_values(self):
        """测试默认值"""
        config = DetectionConfig()
        assert config.no_person_threshold == 3
        assert config.person_present_threshold == 2


class TestApiConfig:
    """测试 ApiConfig 数据类"""
    
    def test_default_values(self):
        """测试默认值"""
        config = ApiConfig()
        assert config.host == "0.0.0.0"
        assert config.port == 8000
        assert "*" in config.cors_origins


class TestSerialConfig:
    """测试 SerialConfig 数据类"""
    
    def test_default_values(self):
        """测试默认值"""
        config = SerialConfig()
        assert config.enabled is True
        assert config.port == "/dev/ttyS3"
        assert config.baudrate == 9600
        assert config.poll_interval == 1.0


class TestSystemConfig:
    """测试 SystemConfig 数据类"""
    
    def test_default_values(self):
        """测试默认值"""
        config = SystemConfig()
        assert "监测" in config.name or "动火" in config.name
        assert config.debug is True


class TestTTSConfig:
    """测试 TTSConfig 数据类"""
    
    def test_default_values(self):
        """测试默认值"""
        config = TTSConfig()
        assert config.enabled is True
        assert config.engine == "kokoro"
        assert config.audio_dir == "audio_assets"
        assert config.idle_timeout == 60


class TestRemoteServerConfig:
    """测试 RemoteServerConfig 数据类"""
    
    def test_default_values(self):
        """测试默认值"""
        config = RemoteServerConfig()
        assert config.enabled is False
        assert config.server_url == ""
        assert config.websocket_path == "dhlr/socket"
