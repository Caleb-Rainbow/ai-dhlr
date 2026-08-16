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
    InferenceConfig,
    DetectionConfig,
    ApiConfig,
    VoiceConfig,
    LoggingConfig,
    GpioConfig,
    SystemConfig,
    SerialConfig,
    RemoteServerConfig,
    HotspotConfig,
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
        assert config.serial_index == 1
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




class TestInferenceConfig:
    """测试 InferenceConfig 数据类"""
    
    def test_default_values(self):
        """测试默认值（须与 config/default_config.yaml 模板一致）"""
        config = InferenceConfig()
        assert config.engine == "rknn"
        assert config.model_path == "yolov11m-sim.rknn"
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


class TestRemoteServerConfig:
    """测试 RemoteServerConfig 数据类"""

    def test_default_values(self):
        """测试默认值"""
        config = RemoteServerConfig()
        assert config.enabled is False
        assert config.server_url == ""
        assert config.websocket_path == "dhlr/socket"


class TestHotspotConfig:
    """测试 HotspotConfig 数据类 + 配置解析/序列化往返"""

    def test_default_values(self):
        """默认开机自启热点为开（保持出厂行为）"""
        config = HotspotConfig()
        assert config.auto_start_on_boot is True

    def test_custom_values(self):
        config = HotspotConfig(auto_start_on_boot=False)
        assert config.auto_start_on_boot is False

    def test_appconfig_defaults_hotspot_when_absent(self):
        """AppConfig 未传 hotspot 时 __post_init__ 兜底为默认"""
        from src.utils.config import AppConfig, SystemConfig, InferenceConfig, \
            DetectionConfig, ApiConfig, VoiceConfig, LoggingConfig, GpioConfig
        cfg = AppConfig(
            system=SystemConfig(), inference=InferenceConfig(),
            detection=DetectionConfig(), cameras=[], zones=[],
            api=ApiConfig(), voice=VoiceConfig(),
            logging=LoggingConfig(), gpio=GpioConfig(),
        )
        assert cfg.hotspot is not None
        assert cfg.hotspot.auto_start_on_boot is True

    def test_parse_and_round_trip(self):
        """_parse_config 读 hotspot 段，_to_dict 原样写回"""
        from src.utils.config import ConfigManager
        cm = ConfigManager()
        cfg = cm._parse_config({"hotspot": {"auto_start_on_boot": False}})
        assert cfg.hotspot.auto_start_on_boot is False
        dumped = cm._to_dict(cfg)
        assert dumped["hotspot"] == {"auto_start_on_boot": False}

    def test_parse_default_when_section_absent(self):
        """缺省 hotspot 段时默认 True"""
        from src.utils.config import ConfigManager
        cm = ConfigManager()
        cfg = cm._parse_config({})
        assert cfg.hotspot.auto_start_on_boot is True
