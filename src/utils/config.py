"""
配置管理模块
加载和管理系统配置
"""
import os
import shutil
import time
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
    serial_index: int = 1  # 串口分区索引（从1开始，1对应地址0x01）
    fire_current_threshold: int = 100  # 动火电流阈值（100=1.00A）
    temp_sensor_address: Optional[int] = None  # 温度传感器地址, None 表示未绑定
    camera_ids: List[str] = field(default_factory=list)  # 多摄像头绑定（不分区模式）：任一摄像头检测到人即视为有人

    @property
    def effective_camera_ids(self) -> List[str]:
        """实际生效的摄像头列表：优先使用 camera_ids，为空则回退到单个 camera_id（向后兼容）"""
        return self.camera_ids if self.camera_ids else [self.camera_id]



@dataclass
class AlarmConfig:
    """报警配置（三阶段）"""
    # 三阶段时间配置（秒）
    warning_time: int = 90      # 预警时间
    alarm_time: int = 180       # 报警时间
    action_time: int = 300      # 切电时间

    # 语音播报间隔（秒）
    broadcast_interval: int = 15  # 重复播报间隔

    # 三阶段消息配置
    warning_message: str = "动火区域离人即将超时，请立即回到工作岗位"
    alarm_message: str = "动火区域离人超时，请立即回到工作岗位"
    action_message: str = "动火区域离人超时，已自动切断炉灶电源，请立即现场处理"

    # 温度报警配置
    temp_alarm_threshold: float = 80.0  # 温度报警阈值 (°C)
    temp_alarm_message: str = "温度过高，请立即处理"


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
    log_retention_days: int = 7        # 日志保留天数（按天轮转时清理，0=永不清理）
    snapshot_retention_days: int = 3   # 告警快照保留天数（按文件 mtime 清理，0=永不清理）
    # 控制台(stderr)日志级别：systemd 下 stderr 进 journald 再转 rsyslog 写 /var/log/syslog，
    # INFO 级双写一年可积累数 GB；默认 WARNING 只让告警及以上进系统日志，INFO 仍完整落盘到日志文件
    console_level: str = "WARNING"


@dataclass
class DiskGuardConfig:
    """磁盘空间看门狗配置"""
    enabled: bool = True
    check_interval_seconds: int = 3600  # 巡检周期
    warn_usage_pct: int = 85            # 使用率超过后执行常规清理（按保留天数）并告警
    critical_usage_pct: int = 92        # 使用率超过后激进清理（日志只留当天、快照只留24h）


@dataclass
class GpioConfig:
    """GPIO配置"""
    enabled: bool = True                      # 是否启用 GPIO 输出
    gpio_path: str = "/sys/external_gpio"     # sysfs GPIO 路径
    pin_fire: str = "gpio0"                   # 动火指示灯引脚
    pin_absence: str = "gpio1"                # 离人指示灯引脚
    pin_alarm: str = "gpio2"                  # 报警指示灯引脚
    # 急停按钮（输入）
    estop_enabled: bool = True                # 是否启用急停监听
    pin_estop: str = "gpio10"                 # 急停输入引脚 (IO10 / GPIO3_A2_D / J20)
    estop_active_low: bool = True             # True=按下接地读到低电平触发


@dataclass
class SystemConfig:
    """系统配置"""
    name: str = "动火离人安全监测系统"
    version: str = "0.1.0"
    debug: bool = True
    device_id: str = ""  # 设备唯一ID，首次运行时自动生成
    zone_mode: str = "zoned"  # 监测模式: "zoned"=分区监测, "single"=不分区监测


@dataclass
class SerialConfig:
    """串口配置"""
    enabled: bool = True
    port: str = "/dev/ttyS3"
    baudrate: int = 9600
    poll_interval: float = 1.0  # 轮询间隔（秒）


@dataclass
class HotspotConfig:
    """WiFi 热点配置"""
    # 开机是否自动启动热点（控制设备上的 hotspot-startup.service）
    auto_start_on_boot: bool = True


@dataclass
class DiscoveryConfig:
    """局域网设备发现（UDP 广播）配置"""
    enabled: bool = True                  # 是否启用发现服务
    udp_port: int = 32100                 # UDP 监听/广播端口
    announce_interval: int = 15           # 主动广播周期(秒)，0=仅被动应答不主动广播
    model: str = "DHLR-RK3568"            # 设备型号（广播用）
    require_private_source: bool = True   # 仅应答来源为私有网段的请求


@dataclass
class RemoteServerConfig:
    """远程服务器配置"""
    enabled: bool = False                    # 是否启用远程连接
    server_url: str = ""                     # 服务器地址（含协议和可选端口）
    websocket_path: str = "dhlr/socket"      # WebSocket 路径
    login_path: str = "/login"               # 登录接口路径
    username: str = ""                       # 用户名
    password: str = ""                       # 密码
    token: str = ""                          # 鉴权 Token
    token_expires: int = 0                   # Token 过期时间戳


@dataclass
class AppConfig:
    """应用总配置"""
    system: SystemConfig
    inference: InferenceConfig
    detection: DetectionConfig
    cameras: List[CameraConfig]
    zones: List[ZoneConfig]
    api: ApiConfig
    voice: VoiceConfig
    logging: LoggingConfig
    gpio: GpioConfig
    alarm: AlarmConfig = None      # 三阶段报警配置
    remote: RemoteServerConfig = None  # 远程服务器配置
    serial: SerialConfig = None    # 串口配置
    hotspot: HotspotConfig = None  # WiFi 热点配置
    discovery: DiscoveryConfig = None  # 局域网设备发现配置
    disk_guard: DiskGuardConfig = None  # 磁盘空间看门狗

    def __post_init__(self):
        """初始化可选配置"""
        if self.alarm is None:
            self.alarm = AlarmConfig()
        if self.remote is None:
            self.remote = RemoteServerConfig()
        if self.serial is None:
            self.serial = SerialConfig()
        if self.hotspot is None:
            self.hotspot = HotspotConfig()
        if self.discovery is None:
            self.discovery = DiscoveryConfig()
        if self.disk_guard is None:
            self.disk_guard = DiskGuardConfig()


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
        """加载配置文件
        
        如果 config.yaml 不存在，会自动从 default_config.yaml 复制一份
        """
        if config_path is None:
            # 默认配置文件路径
            base_dir = Path(__file__).parent.parent.parent
            config_path = base_dir / "config" / "config.yaml"
        else:
            config_path = Path(config_path)
        
        self._config_path = config_path
        
        if not config_path.exists():
            # 尝试从 default_config.yaml 复制
            default_config_path = config_path.parent / "default_config.yaml"
            if default_config_path.exists():
                shutil.copy(default_config_path, config_path)
                print(f"已从 {default_config_path} 创建配置文件: {config_path}")
            else:
                raise FileNotFoundError(
                    f"配置文件不存在: {config_path}，"
                    f"且默认配置文件也不存在: {default_config_path}"
                )
        
        with open(config_path, 'r', encoding='utf-8') as f:
            raw_config = yaml.safe_load(f)

        # 配置损坏自愈：空文件/非字典内容（如磁盘满时 save 写一半被截断成 0 字节）
        # 曾导致 _parse_config 对 None 调 .get 而崩溃循环。保留现场后回退默认模板，
        # 让服务先跑起来，具体业务参数由运维在界面上重配。
        if not isinstance(raw_config, dict):
            corrupt_backup = config_path.with_name(
                config_path.name + f'.corrupt-{int(time.time())}')
            try:
                config_path.rename(corrupt_backup)
                print(f"[config] 配置文件无效（空或损坏），已备份为 {corrupt_backup.name}，使用默认配置")
            except OSError:
                pass
            default_config_path = config_path.parent / "default_config.yaml"
            if default_config_path.exists():
                shutil.copy(default_config_path, config_path)
                with open(config_path, 'r', encoding='utf-8') as f:
                    raw_config = yaml.safe_load(f)
            if not isinstance(raw_config, dict):
                raw_config = {}

        self._config = self._parse_config(raw_config)
        self._config = self._migrate_config(self._config)
        return self._config
    
    def _parse_config(self, raw: Dict[str, Any]) -> AppConfig:
        """解析配置字典为配置对象"""
        # 解析系统配置
        system_raw = raw.get('system', {})
        system = SystemConfig(
            name=system_raw.get('name', '动火离人安全监测系统'),
            version=system_raw.get('version', '0.1.0'),
            debug=system_raw.get('debug', True),
            device_id=system_raw.get('device_id', ''),
            zone_mode=system_raw.get('zone_mode', 'zoned')
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
            camera_ids_raw = list(zone_raw.get('camera_ids', []) or [])
            # camera_id 兼容：未配置时回退到 camera_ids 首个（多摄像头场景）
            camera_id = zone_raw.get('camera_id')
            if not camera_id and camera_ids_raw:
                camera_id = camera_ids_raw[0]
            zones.append(ZoneConfig(
                id=zone_raw['id'],
                name=zone_raw.get('name', zone_raw['id']),
                camera_id=camera_id or '',
                roi=roi,
                enabled=zone_raw.get('enabled', True),
                serial_index=zone_raw.get('serial_index', 0),
                fire_current_threshold=zone_raw.get('fire_current_threshold', 100),
                temp_sensor_address=zone_raw.get('temp_sensor_address'),  # None 表示未绑定
                camera_ids=camera_ids_raw
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
            snapshot_dir=log_raw.get('snapshot_dir', 'snapshots'),
            log_retention_days=int(log_raw.get('log_retention_days', 7)),
            snapshot_retention_days=int(log_raw.get('snapshot_retention_days', 3)),
            console_level=log_raw.get('console_level', 'WARNING')
        )

        # 解析磁盘看门狗配置
        guard_raw = raw.get('disk_guard', {})
        disk_guard = DiskGuardConfig(
            enabled=guard_raw.get('enabled', True),
            check_interval_seconds=int(guard_raw.get('check_interval_seconds', 3600)),
            warn_usage_pct=int(guard_raw.get('warn_usage_pct', 85)),
            critical_usage_pct=int(guard_raw.get('critical_usage_pct', 92))
        )
        
        # 解析GPIO配置
        gpio_raw = raw.get('gpio', {})
        gpio = GpioConfig(
            enabled=gpio_raw.get('enabled', True),
            gpio_path=gpio_raw.get('gpio_path', '/sys/external_gpio'),
            pin_fire=gpio_raw.get('pin_fire', 'gpio0'),
            pin_absence=gpio_raw.get('pin_absence', 'gpio1'),
            pin_alarm=gpio_raw.get('pin_alarm', 'gpio2'),
            estop_enabled=gpio_raw.get('estop_enabled', True),
            pin_estop=gpio_raw.get('pin_estop', 'gpio10'),
            estop_active_low=gpio_raw.get('estop_active_low', True)
        )
        
        # 解析报警配置（三阶段）
        alarm_raw = raw.get('alarm', {})
        alarm = AlarmConfig(
            warning_time=alarm_raw.get('warning_time', 90),
            alarm_time=alarm_raw.get('alarm_time', 180),
            action_time=alarm_raw.get('action_time', 300),
            broadcast_interval=alarm_raw.get('broadcast_interval', 15),
            warning_message=alarm_raw.get('warning_message', '动火区域离人即将超时，请立即回到工作岗位'),
            alarm_message=alarm_raw.get('alarm_message', '动火区域离人超时，请立即回到工作岗位'),
            action_message=alarm_raw.get('action_message', '动火区域离人超时，已自动切断炉灶电源，请立即现场处理'),
            temp_alarm_threshold=alarm_raw.get('temp_alarm_threshold', 80.0),
            temp_alarm_message=alarm_raw.get('temp_alarm_message', '温度过高，请立即处理')
        )

        # 解析远程服务器配置
        remote_raw = raw.get('remote', {})
        remote = RemoteServerConfig(
            enabled=remote_raw.get('enabled', False),
            server_url=remote_raw.get('server_url', ''),
            websocket_path=remote_raw.get('websocket_path', 'dhlr/socket'),
            login_path=remote_raw.get('login_path', '/login'),
            username=remote_raw.get('username', ''),
            password=remote_raw.get('password', ''),
            token=remote_raw.get('token', ''),
            token_expires=remote_raw.get('token_expires', 0)
        )
        
        # 解析串口配置
        serial_raw = raw.get('serial', {})
        serial = SerialConfig(
            enabled=serial_raw.get('enabled', True),
            port=serial_raw.get('port', '/dev/ttyS3'),
            baudrate=serial_raw.get('baudrate', 9600),
            poll_interval=serial_raw.get('poll_interval', 1.0)
        )

        # 解析热点配置
        hotspot_raw = raw.get('hotspot', {})
        hotspot = HotspotConfig(
            auto_start_on_boot=hotspot_raw.get('auto_start_on_boot', True)
        )

        # 解析设备发现配置
        discovery_raw = raw.get('discovery', {})
        discovery = DiscoveryConfig(
            enabled=discovery_raw.get('enabled', True),
            udp_port=int(discovery_raw.get('udp_port', 32100)),
            announce_interval=int(discovery_raw.get('announce_interval', 15)),
            model=discovery_raw.get('model', 'DHLR-RK3568'),
            require_private_source=discovery_raw.get('require_private_source', True)
        )

        return AppConfig(
            system=system,
            inference=inference,
            detection=detection,
            cameras=cameras,
            zones=zones,
            api=api,
            voice=voice,
            logging=logging_config,
            gpio=gpio,
            alarm=alarm,
            remote=remote,
            serial=serial,
            hotspot=hotspot,
            discovery=discovery,
            disk_guard=disk_guard
        )

    def _migrate_config(self, config: AppConfig) -> AppConfig:
        """
        配置迁移（处理旧版本配置兼容）

        迁移规则：
        - 旧版本 serial_index 从 0 开始，新版本从 1 开始
        - serial_index = 0 自动迁移为 1
        """
        migrated = False
        for zone in config.zones:
            if zone.serial_index == 0:
                zone.serial_index = 1
                print(f"Zone {zone.id} serial_index migrated: 0 -> 1")
                migrated = True

        # 如果有迁移，自动保存
        if migrated:
            self.save()
            print("Configuration migrated and saved.")

        return config

    @property
    def config(self) -> AppConfig:
        """获取当前配置"""
        if self._config is None:
            self.load()
        return self._config
    
    def save(self) -> None:
        """保存配置到文件（原子写）

        先写同目录临时文件再 os.replace 原子替换：磁盘满时只会在临时文件上
        失败并抛错，现有 config.yaml 不受影响。此前直接 open('w') 截断后写入，
        磁盘满（Errno 28）曾把设备配置文件毁成 0 字节。
        """
        if self._config is None or self._config_path is None:
            return

        raw = self._to_dict(self._config)
        tmp_path = self._config_path.with_name(self._config_path.name + '.tmp')
        try:
            with open(tmp_path, 'w', encoding='utf-8') as f:
                yaml.dump(raw, f, allow_unicode=True, default_flow_style=False)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp_path, self._config_path)
        finally:
            # replace 成功时 tmp 已不存在；失败时清掉半成品，避免残留
            try:
                tmp_path.unlink()
            except OSError:
                pass
    
    def _to_dict(self, config: AppConfig) -> Dict[str, Any]:
        """将配置对象转换为字典"""
        return {
            'system': {
                'name': config.system.name,
                'version': config.system.version,
                'debug': config.system.debug,
                'device_id': config.system.device_id,
                'zone_mode': config.system.zone_mode
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
                    'camera_ids': list(zone.camera_ids),
                    'roi': [list(point) for point in zone.roi],
                    'enabled': zone.enabled,
                    'serial_index': zone.serial_index,
                    'fire_current_threshold': zone.fire_current_threshold,
                    'temp_sensor_address': zone.temp_sensor_address
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
                'snapshot_dir': config.logging.snapshot_dir,
                'log_retention_days': config.logging.log_retention_days,
                'snapshot_retention_days': config.logging.snapshot_retention_days,
                'console_level': config.logging.console_level
            },
            'disk_guard': {
                'enabled': config.disk_guard.enabled,
                'check_interval_seconds': config.disk_guard.check_interval_seconds,
                'warn_usage_pct': config.disk_guard.warn_usage_pct,
                'critical_usage_pct': config.disk_guard.critical_usage_pct
            },
            'gpio': {
                'enabled': config.gpio.enabled,
                'gpio_path': config.gpio.gpio_path,
                'pin_fire': config.gpio.pin_fire,
                'pin_absence': config.gpio.pin_absence,
                'pin_alarm': config.gpio.pin_alarm,
                'estop_enabled': config.gpio.estop_enabled,
                'pin_estop': config.gpio.pin_estop,
                'estop_active_low': config.gpio.estop_active_low
            },
            'alarm': {
                'warning_time': config.alarm.warning_time,
                'alarm_time': config.alarm.alarm_time,
                'action_time': config.alarm.action_time,
                'broadcast_interval': config.alarm.broadcast_interval,
                'warning_message': config.alarm.warning_message,
                'alarm_message': config.alarm.alarm_message,
                'action_message': config.alarm.action_message,
                'temp_alarm_threshold': config.alarm.temp_alarm_threshold,
                'temp_alarm_message': config.alarm.temp_alarm_message
            },
            'remote': {
                'enabled': config.remote.enabled,
                'server_url': config.remote.server_url,
                'websocket_path': config.remote.websocket_path,
                'login_path': config.remote.login_path,
                'username': config.remote.username,
                'password': config.remote.password,
                'token': config.remote.token,
                'token_expires': config.remote.token_expires
            },
            'serial': {
                'enabled': config.serial.enabled,
                'port': config.serial.port,
                'baudrate': config.serial.baudrate,
                'poll_interval': config.serial.poll_interval
            },
            'hotspot': {
                'auto_start_on_boot': config.hotspot.auto_start_on_boot
            },
            'discovery': {
                'enabled': config.discovery.enabled,
                'udp_port': config.discovery.udp_port,
                'announce_interval': config.discovery.announce_interval,
                'model': config.discovery.model,
                'require_private_source': config.discovery.require_private_source
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
