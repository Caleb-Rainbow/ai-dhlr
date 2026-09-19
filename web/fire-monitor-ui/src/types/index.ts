export interface DeviceInfo {
    version: string;
    device_id?: string;
    name?: string;
    debug?: boolean;
    zone_mode?: 'zoned' | 'single';  // 监测模式: zoned=分区监测, single=不分区监测
}

export interface ZoneStatus {
    id: string;
    name: string;
    state: 'idle' | 'active_with_person' | 'active_no_person' | 'warning' | 'alarm' | 'cutoff' | 'temp_alarm';
    enabled: boolean;
    is_fire_on: boolean;
    has_person: boolean;
    warning_remaining: number;
    alarm_remaining: number;
    cutoff_remaining: number;
    camera_id?: string;
    current_value?: number;  // 实时电流值
    temperature?: number;    // 实时温度值
}

export interface PerformanceStats {
    fps: number;
    cpu_percent: number;
    memory_mb: number;
    inference_time_ms: number;
    engine: string;
    npu_load?: number;
}

export interface Camera {
    id: string;
    name: string;
    type: 'usb' | 'rtsp';
    status: 'online' | 'offline' | 'error' | 'connecting';
    width: number;
    height: number;
    fps: number;
    device?: number;
    rtsp_url?: string;
    username?: string;
    password?: string;
}

export interface ZoneConfig {
    id: string;
    name: string;
    camera_id: string;
    camera_ids?: string[];  // 多摄像头绑定（不分区模式）：任一检测到人即视为有人
    enabled: boolean;
    roi: number[][]; // [[x,y], [x,y], ...]
    serial_index?: number;  // 串口分区索引
    fire_current_threshold?: number;  // 动火电流阈值
    current_value?: number;  // 实时电流值
    temp_sensor_address?: number;  // 温度传感器地址
    temperature?: number;  // 实时温度值
}

export interface LogFile {
    name: string;
    size: number;
    modified: number;
}

export interface AlarmSettings {
    warning_time: number;
    alarm_time: number;
    action_time: number;
    broadcast_interval: number;
    warning_message: string;
    alarm_message: string;
    action_message: string;
    temp_alarm_threshold: number;   // 温度报警阈值
    temp_alarm_message: string;     // 温度报警消息
}

// 推理引擎参数（修改后需重启主程序生效）
export interface InferenceSettings {
    engine: 'rknn' | 'pytorch';
    model_path: string;
    confidence_threshold: number;   // 检测置信度阈值 0.01-0.99
    person_class_id: number;
}

// 检测稳定性参数（修改后需重启主程序生效）
export interface DetectionSettings {
    no_person_threshold: number;       // 连续N帧无人才视为离开
    person_present_threshold: number;  // 连续N帧有人才视为在场
}

// 日志参数（修改后需重启主程序生效）
export interface LoggingSettings {
    level: 'DEBUG' | 'INFO' | 'WARNING' | 'ERROR';
    console_level: 'DEBUG' | 'INFO' | 'WARNING' | 'ERROR';
    log_retention_days: number;
    snapshot_retention_days: number;
}

// 磁盘看门狗参数（修改后需重启主程序生效）
export interface DiskGuardSettings {
    enabled: boolean;
    check_interval_seconds: number;
    warn_usage_pct: number;
    critical_usage_pct: number;
}

// 局域网设备发现参数（修改后需重启主程序生效）
export interface DiscoverySettings {
    enabled: boolean;
    announce_interval: number;  // 0=仅被动应答
}

export interface NetworkStatus {
    interface_type: 'wifi' | 'ethernet' | 'unknown';
    interface_name: string;
    ip_address: string;
    signal_strength: number;
    gateway: string;
    is_connected: boolean;
    is_internet_connected: boolean;  // 是否接入互联网（外网）
}

export interface RemoteServerConfig {
    enabled: boolean;
    server_url: string;
    websocket_path: string;
    login_path: string;
    username: string;
    has_token: boolean;
    is_connected: boolean;
    is_connecting: boolean;
    last_error: string;
    reconnect_attempts: number;
}

export interface SerialConfig {
    enabled: boolean;
    port: string;
    baudrate: number;
    poll_interval: number;
    is_open: boolean;
    debug_hex: boolean;
}

export interface LoraConfig {
    id: number;
    channel: number;
}

export interface GpioConfig {
    enabled: boolean;
    gpio_path: string;
    pin_fire: string;
    pin_absence: string;
    pin_alarm: string;
}

// 报警记录上报消息
export interface AlarmRecordUpload {
    type: 'alarm_record_upload';
    msg_id: string;
    timestamp: number;  // 毫秒时间戳
    device_id: string;
    data: {
        zone_id: string;
        zone_name: string;
        alarm_type: 'warning' | 'alarm' | 'cutoff' | 'temp_alarm';
        image?: string;
        message?: string;
        occurred_at: number;  // 毫秒时间戳
        local_snapshot_path?: string;
    };
}

// 报警记录确认
export interface AlarmRecordAck {
    type: 'alarm_record_ack';
    msg_id: string;
    success: boolean;
    record_id?: number;
    error?: string;
}

// 终端会话创建结果（terminal_start）
export interface TerminalStartResult {
    session_id: string;
    shell: string;
    pid: number;
}

// 终端输出推送（terminal_output，定向推送）
export interface TerminalOutputEvent {
    session_id: string;
    chunk_b64: string;  // PTY 原始输出字节的 Base64
}

// 终端会话结束推送（terminal_exit，定向推送）
export interface TerminalExitEvent {
    session_id: string;
    reason: 'exited' | 'stopped' | 'idle_timeout' | 'connection_closed' | 'server_shutdown';
    exit_code?: number;
}
