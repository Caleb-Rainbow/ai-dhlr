export interface DeviceInfo {
    version: string;
    device_id?: string;
    name?: string;
    uptime?: number;
    debug?: boolean;
}

export interface ZoneStatus {
    id: string;
    name: string;
    state: 'idle' | 'active_with_person' | 'active_no_person' | 'warning' | 'alarm' | 'cutoff';
    enabled: boolean;
    is_fire_on: boolean;
    has_person: boolean;
    warning_remaining: number;
    alarm_remaining: number;
    cutoff_remaining: number;
    camera_id?: string;
}

export interface PerformanceStats {
    fps: number;
    cpu_percent: number;
    memory_mb: number;
    inference_time_ms: number;
    engine: string;
    npu_percent?: number;
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
    enabled: boolean;
    roi: number[][]; // [[x,y], [x,y], ...]
    serial_index?: number;  // 串口分区索引
    fire_current_threshold?: number;  // 动火电流阈值
    current_value?: number;  // 实时电流值
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
}

export interface NetworkStatus {
    interface_type: 'wifi' | 'ethernet' | 'unknown';
    interface_name: string;
    ip_address: string;
    signal_strength: number;
    gateway: string;
    is_connected: boolean;
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
}

export interface LoraConfig {
    id: number;
    channel: number;
}
