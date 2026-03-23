# WebSocket 通讯协议规范

## 概述

动火离人安全监测系统使用 WebSocket 实现全双工实时通讯。所有 API 调用都通过 WebSocket 消息进行，系统支持两种链路：

- **本地链路**：设备本地 Web 端 ↔ Python 后端（`ws://localhost:8000/ws/status`）
- **远程链路**：Python 后端 ↔ 远程服务器（`wss://{server}/{path}`）

**两种链路共用同一套协议**，便于统一开发和维护。

***

## 连接地址

| 链路类型 | 地址格式                              | 示例                                  |
| ---- | --------------------------------- | ----------------------------------- |
| 本地链路 | `ws://localhost:{port}/ws/status` | `ws://localhost:8000/ws/status`     |
| 远程链路 | `ws(s)://{server}/{path}`         | `wss://vis.example.com/dhlr/socket` |

远程链路需要在请求头中携带 Token：

```http
Authorization: Bearer eyJhbGciOiJIUzUxMiJ9...
```

***

## 消息类型

### 1. 请求消息 (request)

客户端发起的请求，期待服务端返回对应的响应。

```json
{
    "type": "request",
    "msg_id": "msg_1704614400000_1",
    "action": "get_zones",
    "params": {},
    "target": "all"
}
```

| 字段      | 类型     | 必填 | 说明                                                          |
| ------- | ------ | -- | ----------------------------------------------------------- |
| type    | string | 是  | 固定为 "request"                                               |
| msg\_id | string | 是  | 消息唯一标识，用于匹配响应                                               |
| action  | string | 是  | 请求的操作类型                                                     |
| params  | object | 否  | 操作参数                                                        |
| target  | string | 否  | 消息路由目标：`"local"`（仅本地处理）、`"remote"`（转发到远程）、`"all"`（本地+远程，默认） |

***

### 2. 响应消息 (response)

服务端对请求的响应。

```json
{
    "type": "response",
    "msg_id": "msg_1704614400000_1",
    "success": true,
    "data": [...]
}
```

| 字段      | 类型      | 必填 | 说明             |
| ------- | ------- | -- | -------------- |
| type    | string  | 是  | 固定为 "response" |
| msg\_id | string  | 是  | 对应请求的 msg\_id  |
| success | boolean | 是  | 操作是否成功         |
| data    | any     | 否  | 返回的数据          |
| error   | string  | 否  | 错误信息（失败时）      |

***

### 3. 事件消息 (event / 推送)

服务端主动推送的消息，无需请求。

**状态更新**

```json
{
    "type": "status_update",
    "timestamp": 1704614400000,
    "device_id": "C206B6AF476AEE73",
    "data": [{ "id": "zone_1", "state": "idle", ... }]
}
```

**状态变化**

```json
{
    "type": "state_change",
    "data": {
        "zone_id": "zone_1",
        "zone_name": "灶台1",
        "old_state": "active_no_person",
        "new_state": "warning",
        "timestamp": 1704614400000,
        "message": "状态变化: active_no_person -> warning"
    }
}
```

**报警事件（预警/报警/切电）**

```json
{
    "type": "alarm_event",
    "data": {
        "zone_id": "zone_1",
        "zone_name": "灶台1",
        "alarm_type": "warning",
        "image": "data:image/jpeg;base64,/9j/4AAQSkZJRgABAQEAYABgAAD...",
        "message": "灶台 灶台1 触发预警"
    }
}
```

| 字段               | 类型     | 必填 | 说明                                                      |
| ---------------- | ------ | -- | ------------------------------------------------------- |
| type             | string | 是  | 固定为 "alarm\_event"                                      |
| data             | object | 是  | 报警事件数据                                                  |
| data.zone\_id    | string | 是  | 灶台ID                                                    |
| data.zone\_name  | string | 是  | 灶台名称                                                    |
| data.alarm\_type | string | 是  | 报警类型：warning（预警）、alarm（报警）、cutoff（切电）、temp\_alarm（温度报警） |
| data.image       | string | 否  | 抓拍图片Base64编码（JPEG格式），格式为 `data:image/jpeg;base64,...`   |
| data.message     | string | 否  | 事件消息描述                                                  |

**网络接口信息**

```json
{
    "type": "network_interface",
    "data": {
        "interface_type": "wifi",
        "interface_name": "wlan0",
        "ip_address": "192.168.1.100",
        "signal_strength": 75,
        "gateway": "192.168.1.1",
        "is_connected": true,
        "is_internet_connected": true
    }
}
```

| 字段                           | 类型      | 必填 | 说明                                     |
| ---------------------------- | ------- | -- | -------------------------------------- |
| type                         | string  | 是  | 固定为 "network\_interface"               |
| data                         | object  | 是  | 网络接口信息                                 |
| data.interface\_type         | string  | 是  | 接口类型：`"wifi"`、`"ethernet"`、`"unknown"` |
| data.interface\_name         | string  | 是  | 接口名称（如 `wlan0`、`eth0`）                 |
| data.ip\_address             | string  | 是  | IP 地址                                  |
| data.signal\_strength        | number  | 否  | WiFi 信号强度（0-100，仅 WiFi 有效）             |
| data.gateway                 | string  | 是  | 网关地址                                   |
| data.is\_connected           | boolean | 是  | 是否已连接到局域网                              |
| data.is\_internet\_connected | boolean | 是  | 是否已接入互联网（外网）                           |

> **注意**：此消息为 `get_network` action 的返回值格式，用于表示设备的网络接口状态。

**报警记录上报**

```json
{
    "type": "alarm_record_upload",
    "msg_id": "uuid-string",
    "timestamp": 1704614400000,
    "device_id": "DHLR-001",
    "data": {
        "zone_id": "zone_1",
        "zone_name": "1号灶台",
        "alarm_type": "warning",
        "image": "data:image/jpeg;base64,...",
        "message": "1号灶台 无人看管超过 90 秒",
        "occurred_at": 1704614400000,
        "local_snapshot_path": null
    }
}
```

| 字段                         | 类型     | 必填 | 说明                                                      |
| -------------------------- | ------ | -- | ------------------------------------------------------- |
| type                       | string | 是  | 固定为 "alarm\_record\_upload"                             |
| msg\_id                    | string | 是  | 消息唯一标识                                                  |
| timestamp                  | number | 是  | 消息发送时间（毫秒时间戳）                                           |
| device\_id                 | string | 是  | 设备ID                                                    |
| data                       | object | 是  | 报警记录数据                                                  |
| data.zone\_id              | string | 是  | 灶台ID                                                    |
| data.zone\_name            | string | 是  | 灶台名称                                                    |
| data.alarm\_type           | string | 是  | 报警类型：warning（预警）、alarm（报警）、cutoff（切电）、temp\_alarm（温度报警） |
| data.image                 | string | 否  | 抓拍图片Base64编码（JPEG格式），格式为 `data:image/jpeg;base64,...`   |
| data.message               | string | 否  | 报警消息描述                                                  |
| data.occurred\_at          | number | 是  | 报警发生时间（毫秒时间戳）                                           |
| data.local\_snapshot\_path | string | 否  | 本地截图路径                                                  |

**报警记录确认**

```json
{
    "type": "alarm_record_ack",
    "msg_id": "与请求相同的uuid",
    "success": true,
    "record_id": 12345
}
```

| 字段         | 类型      | 必填 | 说明                       |
| ---------- | ------- | -- | ------------------------ |
| type       | string  | 是  | 固定为 "alarm\_record\_ack" |
| msg\_id    | string  | 是  | 对应请求的 msg\_id            |
| success    | boolean | 是  | 是否成功接收                   |
| record\_id | number  | 否  | 服务器存储的记录ID（成功时）          |
| error      | string  | 否  | 错误信息（失败时）                |

***

### 4. 心跳消息

客户端定期发送心跳保持连接：

```json
{ "type": "ping" }
```

服务端响应：

```json
{ "type": "pong", "timestamp": 1704614400000 }
```

**心跳间隔说明：**

| 链路类型             | 心跳间隔 | 说明                        |
| ---------------- | ---- | ------------------------- |
| 本地链路（前端 → 后端）    | 30 秒 | Web 客户端连接本地后端服务           |
| 远程链路（后端 → 远程服务器） | 10 秒 | 后端连接远程服务器，需要更频繁的心跳以保持连接活跃 |

> 不同心跳间隔的原因：本地链路网络稳定，较长间隔减少开销；远程链路通过公网，较短间隔可更快检测连接断开。

***

### 5. 错误消息 (error)

服务端发送的错误通知，用于通知客户端连接限制等错误。

```json
{
    "type": "error",
    "error": "connection_limit_reached",
    "message": "连接数已达上限 (10)"
}
```

| 字段      | 类型     | 必填 | 说明                          |
| ------- | ------ | -- | --------------------------- |
| type    | string | 是  | 固定为 "error"                 |
| error   | string | 是  | 错误代码                        |
| message | string | 是  | 错误描述                        |
| code    | number | 否  | HTTP 状态码（如 401 表示 Token 失效） |

**常见错误代码：**

| 错误代码                       | 说明                |
| -------------------------- | ----------------- |
| `connection_limit_reached` | 连接数已达上限           |
| `unauthorized`             | 未授权（Token 无效或已过期） |

**错误消息格式说明：**

1. **连接限制错误**：使用 `error` 字段标识错误类型
   ```json
   {
       "type": "error",
       "error": "connection_limit_reached",
       "message": "连接数已达上限 (10)"
   }
   ```
2. **认证失败（401 错误）**：使用 `code` 字段标识 HTTP 状态码
   ```json
   {
       "type": "error",
       "code": 401,
       "message": "Token 无效或已过期"
   }
   ```

> **注意**：401 错误通过 `code: 401` 字段标识，而非 `error: "unauthorized"`。客户端收到 401 错误后应清除本地 Token 并重新登录。

***

## 支持的 Action

### 灶台操作

| Action        | 说明       | 参数                                                                                                                      |
| ------------- | -------- | ----------------------------------------------------------------------------------------------------------------------- |
| `get_zones`   | 获取所有灶台配置 | 无                                                                                                                       |
| `get_zone`    | 获取单个灶台   | `zone_id`                                                                                                               |
| `create_zone` | 创建灶台     | `name`, `camera_id`, `roi?`, `enabled?`, `serial_index?`, `fire_current_threshold?`, `enable_temp_sensor?`              |
| `update_zone` | 更新灶台     | `zone_id`, `name?`, `camera_id?`, `roi?`, `enabled?`, `serial_index?`, `fire_current_threshold?`, `enable_temp_sensor?` |
| `delete_zone` | 删除灶台     | `zone_id`                                                                                                               |
| `reset_zone`  | 重置灶台状态   | `zone_id`                                                                                                               |
| `toggle_fire` | 模拟火焰开关   | `zone_id`, `is_on`                                                                                                      |

**`get_zones`** **返回值：**

```json
[
  {
    "id": "zone_1",
    "name": "灶台1",
    "camera_id": "0",
    "roi": [[100, 100], [200, 100], [200, 200], [100, 200]],
    "enabled": true,
    "serial_index": 1,
    "fire_current_threshold": 100,
    "current_value": 0
  }
]
```

**`create_zone`** **参数说明：**

- `name`：灶台名称（必填）
- `camera_id`：摄像头ID（必填）
- `roi`：感兴趣区域坐标数组，格式 `[[x1,y1], [x2,y2], ...]`（可选，默认为空）
- `enabled`：是否启用（可选，默认为 `true`）
- `serial_index`：电流检测分区索引（可选，默认为 `1`，从1开始，1对应地址0x01）
- `fire_current_threshold`：火焰电流阈值（可选，默认为 `100`）
- `enable_temp_sensor`：是否启用温度传感器（可选，默认为 `false`）。启用时会自动分配传感器地址，需确保此时只接入了一个传感器

**返回值：**

```json
{
  "id": "zone_1",
  "name": "灶台1",
  "camera_id": "0",
  "roi": [[100, 100], [200, 100], [200, 200], [100, 200]],
  "enabled": true,
  "serial_index": 1,
  "fire_current_threshold": 100,
  "temp_sensor_address": 1,
  "temp_sensor_enabled": true
}
```

**`update_zone`** **参数说明：**

- `enable_temp_sensor`：温度传感器开关（可选）。设为 `true` 时自动分配地址，设为 `false` 时解绑传感器

### 摄像头操作

| Action            | 说明             | 参数                                                                                            |
| ----------------- | -------------- | --------------------------------------------------------------------------------------------- |
| `get_cameras`     | 获取所有摄像头        | 无                                                                                             |
| `get_camera`      | 获取单个摄像头        | `camera_id`                                                                                   |
| `create_camera`   | 创建摄像头          | `name`, `type`, `device?`, `rtsp_url?`, `username?`, `password?`, `width?`, `height?`, `fps?` |
| `update_camera`   | 更新摄像头          | `camera_id`, ...                                                                              |
| `delete_camera`   | 删除摄像头          | `camera_id`                                                                                   |
| `get_usb_devices` | 获取 USB 设备列表    | 无                                                                                             |
| `preview_camera`  | 获取摄像头预览 Base64 | `camera_id`                                                                                   |

**`create_camera`** **参数说明：**

- `name`：摄像头名称（必填）
- `type`：摄像头类型，`"usb"` 或 `"rtsp"`（默认 `"rtsp"`）
- `device`：USB设备索引（USB类型必填）
- `rtsp_url`：RTSP地址（RTSP类型必填）
- `username`/`password`：RTSP认证信息（可选）
- `width`/`height`/`fps`：分辨率和帧率（可选，默认 640x480\@30fps）
- `id`：摄像头唯一ID（可选，**不填时系统自动生成从0开始的自增ID**）

### 状态和设置

| Action                 | 说明            | 参数                                                                              |
| ---------------------- | ------------- | ------------------------------------------------------------------------------- |
| `get_status`           | 获取所有灶台状态      | 无                                                                               |
| `get_device`           | 获取设备信息        | 无                                                                               |
| `get_performance`      | 获取性能指标        | 无                                                                               |
| `get_snapshot`         | 获取告警快照 Base64 | `filename`                                                                      |
| `get_settings`         | 获取系统设置        | `category?`                                                                     |
| `update_settings`      | 更新系统设置        | `category`, `settings`                                                          |
| `set_device_id`        | 设置设备ID        | `device_id`                                                                     |
| `get_network`          | 获取网络状态        | 无                                                                               |
| `get_remote_config`    | 获取远程配置        | 无                                                                               |
| `update_remote_config` | 更新远程配置        | `enabled`, `server_url`, `websocket_path`, `login_path`, `username`, `password` |
| `verify_remote_login`  | 校验远程登录        | `server_url`, `username`, `password`                                            |

**`get_device`** **返回值：**

```json
{
  "name": "动火离人安全监测系统",
  "version": "0.1.0",
  "device_id": "DHLR001",
  "platform": "Linux",
  "python_version": "3.10.0",
  "zone_mode": "zoned",
  "default_serial_index": 1
}
```

**`set_device_id`** **参数说明：**

- `device_id`: 新的设备ID，只允许大写字母和数字，长度1-32位

### 语音音量

| Action       | 说明       | 参数                 | 返回                  |
| ------------ | -------- | ------------------ | ------------------- |
| `get_volume` | 获取当前语音音量 | 无                  | `{volume: float}`   |
| `set_volume` | 设置语音音量   | `volume` (0.0-1.0) | `{volume, message}` |

**说明：**

- `volume`：音量值范围为 0.0（静音）到 1.0（最大音量）
- 设置后会立即生效并持久化到配置文件

### 监测模式

| Action                     | 说明       | 参数                     | 返回                                |
| -------------------------- | -------- | ---------------------- | --------------------------------- |
| `get_zone_mode`            | 获取当前监测模式 | 无                      | `{zone_mode, zone_count}`         |
| `set_zone_mode`            | 设置监测模式   | `zone_mode`            | `{zone_mode, message}`            |
| `get_default_serial_index` | 获取默认串口索引 | 无                      | `{default_serial_index}`          |
| `set_default_serial_index` | 设置默认串口索引 | `default_serial_index` | `{default_serial_index, message}` |

**监测模式说明：**

- `zone_mode`：监测模式，可选值：
  - `"zoned"`：分区监测（默认），支持多个独立灶台区域
  - `"single"`：不分区监测，仅支持单一监测区域
- `zone_count`：当前灶台数量

**切换限制：**

- 切换监测模式前必须删除所有灶台区域，否则会返回错误
- 不分区模式下，灶台数量限制为 1，灶台名称固定为"灶台区域"

**默认串口索引说明：**

- `default_serial_index`：不分区模式下的默认串口索引
- 索引从1开始，1对应MODBUS地址0x01
- 用于不分区模式下新建灶台时的默认值

**示例请求：**

```json
{
  "type": "request",
  "msg_id": "xxx",
  "action": "set_zone_mode",
  "params": {
    "zone_mode": "single"
  }
}
```

**成功响应：**

```json
{
  "type": "response",
  "msg_id": "xxx",
  "success": true,
  "data": {
    "zone_mode": "single",
    "message": "已切换到不分区监测模式"
  }
}
```

**失败响应（存在灶台时）：**

```json
{
  "type": "response",
  "msg_id": "xxx",
  "success": false,
  "error": "请先删除全部灶台区域后再切换到不分区监测模式"
}
```

### 日志

| Action            | 说明       | 参数                    |
| ----------------- | -------- | --------------------- |
| `get_log_files`   | 获取日志文件列表 | 无                     |
| `get_log_content` | 读取日志内容   | `filename?`, `lines?` |

### 串口通讯

| Action                 | 说明          | 参数                                                 | 返回                                                             |
| ---------------------- | ----------- | -------------------------------------------------- | -------------------------------------------------------------- |
| `get_serial_ports`     | 获取系统可用的串口列表 | 无                                                  | `[{device, name, description, hwid}, ...]`                     |
| `get_serial_config`    | 获取串口配置      | 无                                                  | `{enabled, port, baudrate, poll_interval, is_open, debug_hex}` |
| `update_serial_config` | 更新串口配置      | `enabled?`, `port?`, `baudrate?`, `poll_interval?` | `{message}`                                                    |
| `get_currents`         | 获取所有分区电流值   | 无                                                  | `{currents: {zone_id: value, ...}}`                            |
| `get_lora_config`      | 获取LoRa配置    | 无                                                  | `{id, channel}`                                                |
| `set_lora_config`      | 设置LoRa配置    | `id?`, `channel?`                                  | `{message}`                                                    |
| `set_serial_debug`     | 设置串口调试日志开关  | `enabled`                                          | `{enabled, message}`                                           |

**`set_serial_debug`** **说明：**

- 开启后，串口发送和接收的数据将以16进制格式打印到日志中
- 日志格式：`[TX] FF AA FF 01 03 00 30 00 01 84 05`（发送）、`[RX] 01 03 02 00 05 XX XX`（接收）
- 用于调试串口通讯问题

### GPIO 控制

| Action               | 说明             | 参数                                                    | 返回                                                       |
| -------------------- | -------------- | ----------------------------------------------------- | -------------------------------------------------------- |
| `get_gpio_pins`      | 获取可用 GPIO 引脚列表 | 无                                                     | `{pins: ["gpio0", "gpio1", ...]}`                        |
| `get_gpio_config`    | 获取 GPIO 配置     | 无                                                     | `{enabled, gpio_path, pin_fire, pin_absence, pin_alarm}` |
| `update_gpio_config` | 更新 GPIO 配置     | `enabled?`, `pin_fire?`, `pin_absence?`, `pin_alarm?` | `{message}`                                              |

**说明：**

- GPIO 通过 sysfs 接口控制，路径默认为 `/sys/external_gpio`
- 三个指示灯分别表示：动火状态（fire）、离人状态（absence）、报警状态（alarm）
- 只要有任意区域处于对应状态，相应指示灯就会亮起

### 巡检操作

| Action              | 说明     | 参数 | 返回                   |
| ------------------- | ------ | -- | -------------------- |
| `start_patrol`      | 开始巡检模式 | 无  | `{success, message}` |
| `stop_patrol`       | 退出巡检模式 | 无  | `{success, message}` |
| `get_patrol_status` | 获取巡检状态 | 无  | 巡检状态对象               |

#### 单灶台操作

| Action                | 说明                     | 参数         | 返回                               |
| --------------------- | ---------------------- | ---------- | -------------------------------- |
| `patrol_check_person` | 检测单个灶台的离人状态            | `zone_id`  | `{success, has_person, message}` |
| `patrol_check_fire`   | 检测单个灶台的动火状态            | `zone_id`  | `{success, is_fire_on, message}` |
| `patrol_alarm_demo`   | 报警演示（预警→报警→切电，每步间隔10秒） | `zone_id?` | `{success, message}`             |
| `patrol_cutoff_zone`  | 单灶台切电                  | `zone_id`  | `{success, message}`             |

> **说明**：
>
> - `patrol_alarm_demo` 的 `zone_id` 参数为可选。不传时自动选择第一个动火灶台，传值时对指定灶台执行报警演示
> - 执行报警演示的灶台需要处于动火状态

#### 全局强制动作

| Action                 | 说明             | 参数 | 返回                   |
| ---------------------- | -------------- | -- | -------------------- |
| `patrol_self_check`    | 设备自检（批量检测所有灶台） | 无  | `{success, message}` |
| `patrol_force_warning` | 强制预警（所有区）      | 无  | `{success, message}` |
| `patrol_force_alarm`   | 强制报警（所有区）      | 无  | `{success, message}` |
| `patrol_force_cutoff`  | 强制切电（所有区）      | 无  | `{success, message}` |

**巡检事件推送**

```json
{
    "type": "patrol_event",
    "event_type": "status_update",
    "timestamp": 1704614400000,
    "data": {
        "is_active": true,
        "current_step": "self_check_person",
        "progress": 50,
        "message": "正在检测灶台1有人状态...",
        "results": [...]
    }
}
```

### 系统更新

| Action           | 说明     | 参数 | 返回                   |
| ---------------- | ------ | -- | -------------------- |
| `trigger_update` | 触发系统更新 | 无  | `{success, message}` |

**说明：**

- 该接口会执行服务器上的 `update.sh` 脚本
- 脚本将拉取最新的 Git 代码并重启服务
- 执行后 WebSocket 连接会断开，需要等待服务重启后重新连接
- 建议在调用前向用户显示确认对话框

**调用示例：**

```typescript
const result = await ws.request<{ success: boolean; message: string }>('trigger_update');
// result: { success: true, message: "更新脚本已触发，服务即将重启..." }
```

### 依赖安装

| Action                | 说明       | 参数 | 返回                                   |
| --------------------- | -------- | -- | ------------------------------------ |
| `install_dependencies` | 安装/更新依赖 | 无  | `{success, message, output?}` |

**说明：**

- 该接口执行 `pip install -r requirements.txt` 命令
- 安装过程可能需要数分钟，取决于网络状况和依赖数量
- 建议在调用前向用户显示确认对话框
- `output` 字段包含 pip 命令的详细输出（仅当有输出时返回）

**调用示例：**

```typescript
const result = await ws.request<{ success: boolean; message: string; output?: string }>('install_dependencies');
// result: { success: true, message: "依赖安装成功", output: "Collecting fastapi..." }
```

**错误响应示例：**

```json
{
    "type": "response",
    "msg_id": "xxx",
    "success": false,
    "error": "安装失败 (exit code 1): ERROR: Could not find a version..."
}
```

***

## 重连策略

### 指数退避算法

| 重连次数 | 延迟时间   |
| ---- | ------ |
| 1    | 1秒     |
| 2    | 2秒     |
| 3    | 4秒     |
| 4+   | 最大 30秒 |

### Token 失效处理

收到 401 错误时自动调用 `/login` 获取新 Token 后重连。

***

## 前端使用示例

```typescript
import { ws } from './api/ws';

// 连接
await ws.connect();

// 发送请求
const zones = await ws.request<Zone[]>('get_zones');

// 监听事件
ws.on('status_update', (data) => {
    console.log('状态更新:', data);
});

// 监听连接状态
ws.on('connect', () => console.log('已连接'));
ws.on('disconnect', () => console.log('已断开'));
```

