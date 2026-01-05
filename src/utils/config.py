"""
配置管理模块
加载和管理系统配置
"""
import os
import yaml
from dataclasses import dataclass, field
from typing import List, Dict, Any, Optional, Tuple
from pathlib import Path


@dataclass
class CameraConfig:
    """摄像头配置"""
    id: str
    type: str  # usb 或 rtsp
    name: str
    device: Optional[int] = None  # USB设备索引
    rtsp_url: Optional[str] = None  # RTSP地址
    username: Optional[str] = None  # RTSP认证用户名
    password: Optional[str] = None  # RTSP认证密码
    width: int = 640
    height: int = 480
    fps: int = 30


@dataclass
class ZoneConfig:
    """灶台/区域配置"""
    id: str
    name: str
    camera_id: str
    roi: List[Tuple[float, float]]  # 归一化坐标列表
    enabled: bool = True


@dataclass
class SafetyConfig:
    """安全/超时配置"""
    warning_timeout: int = 30  # 预警超时（秒）
    cutoff_timeout: int = 60  # 切电超时（秒）


@dataclass
class InferenceConfig:
    """推理引擎配置"""
    engine: str = "pytorch"  # pytorch 或 rknn
    model_path: str = "yolo11n.pt"
    confidence_threshold: float = 0.5
    person_class_id: int = 0


@dataclass
class DetectionConfig:
    """检测稳定性配置"""
    no_person_threshold: int = 3  # 连续N帧无人才视为离开
    person_present_threshold: int = 2  # 连续N帧有人才视为在场


@dataclass
class ApiConfig:
    """API服务配置"""
    host: str = "0.0.0.0"
    port: int = 8000
    cors_origins: List[str] = field(default_factory=lambda: ["*"])


@dataclass
class VoiceConfig:
    """语音播报配置"""
    enabled: bool = True
    engine: str = "pyttsx3"
    rate: int = 150
    volume: float = 1.0


@dataclass
class LoggingConfig:
    """日志配置"""
    level: str = "INFO"
    log_dir: str = "logs"
    snapshot_dir: str = "snapshots"


@dataclass
class GpioConfig:
    """GPIO配置"""
    simulated: bool = True  # Demo阶段模拟


@dataclass
class SystemConfig:
    """系统配置"""
    name: str = "动火离人安全监测系统"
    version: str = "0.1.0"
    debug: bool = True


@dataclass
class AppConfig:
    """应用总配置"""
    system: SystemConfig
    safety: SafetyConfig
    inference: InferenceConfig
    detection: DetectionConfig
    cameras: List[CameraConfig]
    zones: List[ZoneConfig]
    api: ApiConfig
    voice: VoiceConfig
    logging: LoggingConfig
    gpio: GpioConfig


class ConfigManager:
    """配置管理器"""
    
    _instance: Optional['ConfigManager'] = None
    _config: Optional[AppConfig] = None
    _config_path: Optional[Path] = None
    
    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance
    
    def load(self, config_path: str = None) -> AppConfig:
        """加载配置文件"""
        if config_path is None:
            # 默认配置文件路径
            base_dir = Path(__file__).parent.parent.parent
            config_path = base_dir / "config" / "config.yaml"
        else:
            config_path = Path(config_path)
        
        self._config_path = config_path
        
        if not config_path.exists():
            raise FileNotFoundError(f"配置文件不存在: {config_path}")
        
        with open(config_path, 'r', encoding='utf-8') as f:
            raw_config = yaml.safe_load(f)
        
        self._config = self._parse_config(raw_config)
        return self._config
    
    def _parse_config(self, raw: Dict[str, Any]) -> AppConfig:
        """解析配置字典为配置对象"""
        # 解析系统配置
        system_raw = raw.get('system', {})
        system = SystemConfig(
            name=system_raw.get('name', '动火离人安全监测系统'),
            version=system_raw.get('version', '0.1.0'),
            debug=system_raw.get('debug', True)
        )
        
        # 解析安全配置
        safety_raw = raw.get('safety', {})
        safety = SafetyConfig(
            warning_timeout=safety_raw.get('warning_timeout', 30),
            cutoff_timeout=safety_raw.get('cutoff_timeout', 60)
        )
        
        # 解析推理配置
        inf_raw = raw.get('inference', {})
        inference = InferenceConfig(
            engine=inf_raw.get('engine', 'pytorch'),
            model_path=inf_raw.get('model_path', 'yolo11n.pt'),
            confidence_threshold=inf_raw.get('confidence_threshold', 0.5),
            person_class_id=inf_raw.get('person_class_id', 0)
        )
        
        # 解析检测配置
        det_raw = raw.get('detection', {})
        detection = DetectionConfig(
            no_person_threshold=det_raw.get('no_person_threshold', 3),
            person_present_threshold=det_raw.get('person_present_threshold', 2)
        )
        
        # 解析摄像头配置
        cameras = []
        for cam_raw in raw.get('cameras', []):
            cameras.append(CameraConfig(
                id=cam_raw['id'],
                type=cam_raw.get('type', 'usb'),
                name=cam_raw.get('name', cam_raw['id']),
                device=cam_raw.get('device'),
                rtsp_url=cam_raw.get('rtsp_url'),
                username=cam_raw.get('username'),
                password=cam_raw.get('password'),
                width=cam_raw.get('width', 640),
                height=cam_raw.get('height', 480),
                fps=cam_raw.get('fps', 30)
            ))
        
        # 解析灶台配置
        zones = []
        for zone_raw in raw.get('zones', []):
            roi = [tuple(point) for point in zone_raw.get('roi', [])]
            zones.append(ZoneConfig(
                id=zone_raw['id'],
                name=zone_raw.get('name', zone_raw['id']),
                camera_id=zone_raw['camera_id'],
                roi=roi,
                enabled=zone_raw.get('enabled', True)
            ))
        
        # 解析API配置
        api_raw = raw.get('api', {})
        api = ApiConfig(
            host=api_raw.get('host', '0.0.0.0'),
            port=api_raw.get('port', 8000),
            cors_origins=api_raw.get('cors_origins', ['*'])
        )
        
        # 解析语音配置
        voice_raw = raw.get('voice', {})
        voice = VoiceConfig(
            enabled=voice_raw.get('enabled', True),
            engine=voice_raw.get('engine', 'pyttsx3'),
            rate=voice_raw.get('rate', 150),
            volume=voice_raw.get('volume', 1.0)
        )
        
        # 解析日志配置
        log_raw = raw.get('logging', {})
        logging_config = LoggingConfig(
            level=log_raw.get('level', 'INFO'),
            log_dir=log_raw.get('log_dir', 'logs'),
            snapshot_dir=log_raw.get('snapshot_dir', 'snapshots')
        )
        
        # 解析GPIO配置
        gpio_raw = raw.get('gpio', {})
        gpio = GpioConfig(
            simulated=gpio_raw.get('simulated', True)
        )
        
        return AppConfig(
            system=system,
            safety=safety,
            inference=inference,
            detection=detection,
            cameras=cameras,
            zones=zones,
            api=api,
            voice=voice,
            logging=logging_config,
            gpio=gpio
        )
    
    @property
    def config(self) -> AppConfig:
        """获取当前配置"""
        if self._config is None:
            self.load()
        return self._config
    
    def save(self) -> None:
        """保存配置到文件"""
        if self._config is None or self._config_path is None:
            return
        
        raw = self._to_dict(self._config)
        with open(self._config_path, 'w', encoding='utf-8') as f:
            yaml.dump(raw, f, allow_unicode=True, default_flow_style=False)
    
    def _to_dict(self, config: AppConfig) -> Dict[str, Any]:
        """将配置对象转换为字典"""
        return {
            'system': {
                'name': config.system.name,
                'version': config.system.version,
                'debug': config.system.debug
            },
            'safety': {
                'warning_timeout': config.safety.warning_timeout,
                'cutoff_timeout': config.safety.cutoff_timeout
            },
            'inference': {
                'engine': config.inference.engine,
                'model_path': config.inference.model_path,
                'confidence_threshold': config.inference.confidence_threshold,
                'person_class_id': config.inference.person_class_id
            },
            'detection': {
                'no_person_threshold': config.detection.no_person_threshold,
                'person_present_threshold': config.detection.person_present_threshold
            },
            'cameras': [
                {
                    'id': cam.id,
                    'type': cam.type,
                    'name': cam.name,
                    'device': cam.device,
                    'rtsp_url': cam.rtsp_url,
                    'username': cam.username,
                    'password': cam.password,
                    'width': cam.width,
                    'height': cam.height,
                    'fps': cam.fps
                }
                for cam in config.cameras
            ],
            'zones': [
                {
                    'id': zone.id,
                    'name': zone.name,
                    'camera_id': zone.camera_id,
                    'roi': [list(point) for point in zone.roi],
                    'enabled': zone.enabled
                }
                for zone in config.zones
            ],
            'api': {
                'host': config.api.host,
                'port': config.api.port,
                'cors_origins': config.api.cors_origins
            },
            'voice': {
                'enabled': config.voice.enabled,
                'engine': config.voice.engine,
                'rate': config.voice.rate,
                'volume': config.voice.volume
            },
            'logging': {
                'level': config.logging.level,
                'log_dir': config.logging.log_dir,
                'snapshot_dir': config.logging.snapshot_dir
            },
            'gpio': {
                'simulated': config.gpio.simulated
            }
        }
    
    def update_zones(self, zones: List[ZoneConfig]) -> None:
        """更新灶台配置"""
        if self._config:
            self._config.zones = zones
            self.save()
    
    def add_camera(self, camera: CameraConfig) -> None:
        """添加摄像头"""
        if self._config:
            self._config.cameras.append(camera)
            self.save()
    
    def remove_camera(self, camera_id: str) -> bool:
        """删除摄像头"""
        if self._config:
            original_len = len(self._config.cameras)
            self._config.cameras = [c for c in self._config.cameras if c.id != camera_id]
            if len(self._config.cameras) < original_len:
                self.save()
                return True
        return False


# 全局配置管理器实例
config_manager = ConfigManager()


def get_config() -> AppConfig:
    """获取全局配置"""
    return config_manager.config
