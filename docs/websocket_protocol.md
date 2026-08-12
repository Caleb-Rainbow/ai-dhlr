# WebSocket 通讯协议规范

## 概述

动火离人安全监测系统使用 WebSocket 实现全双工实时通讯。所有 API 调用都通过 WebSocket 消息进行，系统支持两种链路：

- **本地链路**：设备本地 Web 端 ↔ Python 后端（`ws://localhost:8000/ws/status`）
- **远程链路**：Python 后端 ↔ 远程服务器（`ws(s)://{host}{path}/{deviceId}?token={jwt}`）

**两种链路共用同一套协议**，便于统一开发和维护。

***

## 连接

### 本地链路

| 项目 | 说明 |
|------|------|
| 地址格式 | `ws://localhost:{port}/ws/status` |
| 示例 | `ws://localhost:8000/ws/status` |
| 认证 | 无需认证 |
| 最大连接数 | 10（超出时服务端发送错误并以 WebSocket 关闭码 `1013` 断开） |
| 心跳间隔 | 30 秒（客户端发送 `ping`） |

### 远程链路

#### 登录获取 Token

设备启动时向远程服务器的登录接口发送 HTTP POST 请求获取 JWT Token。

**请求：**

```http
POST {login_path} HTTP/1.1
Content-Type: application/json

{
    "username": "admin",
    "password": "******"
}
```

**期望响应：**

```json
{
    "code": 200,
    "token": "eyJhbGciOiJIUzUxMiJ9...",
    "expires_in": 86400
}
```

| 字段 | 类型 | 说明 |
|------|------|------|
| code | number | 业务状态码，`200` 表示成功 |
| token | string | JWT Token |
| expires_in | number | 可选。可以是以下三种格式之一：相对秒数（如 `86400`）、Unix 秒级时间戳（如 `1704700800`）、Unix 毫秒级时间戳（如 `1704700800000`）。未提供时默认有效期 24 小时 |

**错误响应：** HTTP 401 表示用户名或密码错误。登录请求超时时间为 10 秒。

#### WebSocket 连接

登录成功后，设备使用获取的 Token 建立 WebSocket 连接。

| 项目 | 说明 |
|------|------|
| 地址格式 | `ws(s)://{host}{websocket_path}/{deviceId}?token={jwt}` |
| 示例 | `wss://vis.example.com/ws/dhlr/device/DHLR001?token=eyJhbGci...` |
| 认证 | Token 通过 URL 查询参数 `token` 传递 |
| 心跳间隔 | 10 秒（设备发送 `ping`，不可配置） |
| 接收超时 | 30 秒（超过未收到消息则断开） |

> **对接方注意：** 远程服务器需要实现两个 WebSocket 端点：
> - 设备端：`{websocket_path}/{deviceId}` — 设备连接此端点，Token 通过 query 参数传递
> - 客户端端：`/ws/dhlr/client/{deviceId}` — 前端 Web 连接此端点

#### Token 失效处理

当设备收到 `code: 401` 的错误消息时，会自动清除本地 Token 并重新调用登录接口获取新 Token，然后重新建立 WebSocket 连接。

#### 离线消息缓存

当远程连接断开期间，设备产生的报警记录会缓存在本地队列中（最多 100 条）。设备重新连接后会自动补发缓存的消息。**对接方服务器应具备处理延迟到达的历史报警记录的能力。**

***

## 消息格式

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

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| type | string | 是 | 固定为 `"request"` |
| msg_id | string | 否 | 消息唯一标识，用于匹配响应。未提供时服务端自动生成 UUID |
| action | string | 是 | 请求的操作类型，见下方 Action 列表 |
| params | object | 否 | 操作参数，默认为 `{}` |
| target | string | 否 | 消息路由目标，默认 `"all"` |

**target 路由说明：**

| 值 | 行为 |
|----|------|
| `"local"` | 仅本地处理，不转发到远程服务器 |
| `"remote"` | 仅转发到远程服务器处理 |
| `"all"` | 本地处理并转发到远程服务器（默认） |

> **注意：** `target` 仅对事件消息（`type: "request"` 以外的消息）生效。`request` 类型的消息始终在本地处理，响应仅返回给发起请求的客户端。

### 2. 响应消息 (response)

服务端对请求的响应。

```json
{
    "type": "response",
    "msg_id": "msg_1704614400000_1",
    "success": true,
    "data": {}
}
```

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| type | string | 是 | 固定为 `"response"` |
| msg_id | string | 是 | 对应请求的 msg_id |
| success | boolean | 是 | 操作是否成功 |
| data | any | 否 | 成功时返回的数据 |
| error | string | 否 | 失败时的错误信息 |

**错误响应示例：**

```json
{
    "type": "response",
    "msg_id": "msg_1704614400000_1",
    "success": false,
    "error": "缺少 zone_id 参数"
}
```

常见错误场景：
- 缺少必填参数：`"缺少 xxx 参数"`
- 资源不存在：`"xxx 'yyy' 不存在"`
- 参数校验失败：`"xxx 必须是 yyy"`

### 3. 事件消息 (推送)

服务端主动推送的消息，无需客户端请求。所有广播消息会自动注入 `timestamp`（毫秒时间戳）和 `device_id` 字段（如果消息中不存在）。

#### status_update — 状态更新

周期性推送所有灶台的实时状态。`data` 数组中每个元素的字段见下方 `get_status` 返回值。

```json
{
    "type": "status_update",
    "timestamp": 1704614400000,
    "device_id": "C206B6AF476AEE73",
    "data": [
        {
            "id": "zone_1",
            "name": "灶台1",
            "camera_id": "0",
            "roi": [[0.1, 0.1], [0.5, 0.1], [0.5, 0.5], [0.1, 0.5]],
            "enabled": true,
            "state": "idle",
            "is_fire_on": false,
            "has_person": false,
            "no_person_duration": 0.0,
            "warning_remaining": 0.0,
            "alarm_remaining": 0.0,
            "cutoff_remaining": 0.0,
            "current_value": 0,
            "temperature": 0.0,
            "last_snapshot_path": null
        }
    ]
}
```

#### state_change — 状态变化

当灶台状态发生切换时触发。

```json
{
    "type": "state_change",
    "timestamp": 1704614400000,
    "device_id": "C206B6AF476AEE73",
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

| 字段 | 类型 | 说明 |
|------|------|------|
| data.zone_id | string | 灶台 ID |
| data.zone_name | string | 灶台名称 |
| data.old_state | string | 变化前的状态 |
| data.new_state | string | 变化后的状态 |
| data.timestamp | number | 变化发生的毫秒时间戳 |
| data.message | string | 变化描述 |

#### alarm_event — 报警事件

灶台触发报警（预警、报警、切电、温度报警）或急停按钮按下时推送。

```json
{
    "type": "alarm_event",
    "timestamp": 1704614400000,
    "device_id": "C206B6AF476AEE73",
    "data": {
        "zone_id": "zone_1",
        "zone_name": "灶台1",
        "alarm_type": "warning",
        "image": "data:image/jpeg;base64,/9j/4AAQSkZJRgABAQEAYABgAAD...",
        "message": "灶台 灶台1 触发预警"
    }
}
```

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| data.zone_id | string | 是 | 灶台 ID |
| data.zone_name | string | 是 | 灶台名称 |
| data.alarm_type | string | 是 | 报警类型：`"warning"`（预警）、`"alarm"`（报警）、`"cutoff"`（切电）、`"temp_alarm"`（温度报警）、`"estop"`（急停） |
| data.image | string | 否 | 抓拍图片 Base64 编码，格式 `data:image/jpeg;base64,...` |
| data.message | string | 否 | 事件描述 |

> **急停事件（`alarm_type: "estop"`）说明：** 当 GPIO 急停按钮（默认 `pin_estop=gpio10`）触发时推送，此时 `zone_id="all"`、`zone_name="全部灶台"`、`image` 为空，表示已对所有启用灶台执行全局切电。急停事件**不上报**到远程服务器。

#### network_interface — 网络状态变化

网络监测模块每 10 秒检测一次网络状态，状态变化时推送。字段与 `get_network` 返回值相同。

```json
{
    "type": "network_interface",
    "timestamp": 1704614400000,
    "device_id": "C206B6AF476AEE73",
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

| 字段 | 类型 | 说明 |
|------|------|------|
| data.interface_type | string | 接口类型：`"wifi"`、`"ethernet"`、`"unknown"` |
| data.interface_name | string | 接口名称（如 `wlan0`、`eth0`） |
| data.ip_address | string | IP 地址 |
| data.signal_strength | number | WiFi 信号强度 0-100；非 WiFi 时为 `-1` |
| data.gateway | string | 网关地址 |
| data.is_connected | boolean | 是否已连接到局域网（IP 非 `127.0.0.1`） |
| data.is_internet_connected | boolean | 是否已接入互联网（通过 DNS 探测） |

#### patrol_event — 巡检事件

巡检过程中推送进度和结果。

```json
{
    "type": "patrol_event",
    "timestamp": 1704614400000,
    "device_id": "C206B6AF476AEE73",
    "event_type": "status_update",
    "data": {
        "is_active": true,
        "current_step": "self_check_person",
        "progress": 50,
        "message": "正在检测灶台1有人状态...",
        "results": []
    }
}
```

| 字段 | 类型 | 说明 |
|------|------|------|
| event_type | string | `"status_update"`（进度更新）或 `"result"`（单步结果） |
| data.is_active | boolean | 巡检是否正在进行 |
| data.current_step | string | 当前巡检步骤，见巡检步骤枚举 |
| data.progress | number | 进度百分比 0-100 |
| data.message | string | 当前步骤描述 |
| data.results | array | 巡检结果列表（最多保留 20 条） |

**巡检步骤枚举（`current_step`）：**

| 值 | 说明 |
|----|------|
| `"idle"` | 空闲 |
| `"self_check_person"` | 自检-离人检测 |
| `"self_check_fire"` | 自检-动火检测 |
| `"alarm_demo"` | 报警演示 |
| `"force_warning"` | 强制预警 |
| `"force_alarm"` | 强制报警 |
| `"force_cutoff"` | 强制切电 |

**巡检结果项（`results` 数组元素）：**

| 字段 | 类型 | 说明 |
|------|------|------|
| zone_id | string | 灶台 ID |
| zone_name | string | 灶台名称 |
| step | string | 步骤描述（如 `"离人检测"`、`"动火检测"`） |
| status | string | `"success"`、`"warning"`、`"error"` |
| message | string | 结果描述 |
| timestamp | number | 毫秒时间戳 |

#### alarm_record_upload — 报警记录上报（设备→服务器）

设备向远程服务器上报报警记录。**此消息仅从设备发送到远程服务器，不会广播到本地客户端。** 远程服务器收到后应回复 `alarm_record_ack`。

```json
{
    "type": "alarm_record_upload",
    "msgId": "uuid-string",
    "timestamp": 1704614400000,
    "deviceId": "DHLR-001",
    "data": {
        "zoneId": "zone_1",
        "zoneName": "1号灶台",
        "alarmType": "warning",
        "image": "data:image/jpeg;base64,...",
        "message": "1号灶台 无人看管超过 90 秒",
        "occurredAt": 1704614400000,
        "localSnapshotPath": null
    }
}
```

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| type | string | 是 | 固定为 `"alarm_record_upload"` |
| msgId | string | 是 | 消息唯一标识（UUID） |
| timestamp | number | 是 | 消息发送时间（毫秒时间戳） |
| deviceId | string | 是 | 设备 ID |
| data.zoneId | string | 是 | 灶台 ID |
| data.zoneName | string | 是 | 灶台名称 |
| data.alarmType | string | 是 | 报警类型：`"warning"`、`"alarm"`、`"cutoff"`、`"temp_alarm"` |
| data.image | string | 否 | 抓拍图片 Base64 编码 |
| data.message | string | 否 | 报警描述 |
| data.occurredAt | number | 是 | 报警发生时间（毫秒时间戳） |
| data.localSnapshotPath | string | 否 | 设备本地截图文件路径 |

#### alarm_record_ack — 报警记录确认（服务器→设备）

远程服务器对 `alarm_record_upload` 的确认响应。**此消息仅从远程服务器发送到设备。**

```json
{
    "type": "alarm_record_ack",
    "msg_id": "与请求相同的uuid",
    "success": true,
    "record_id": 12345
}
```

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| type | string | 是 | 固定为 `"alarm_record_ack"` |
| msg_id | string | 是 | 对应上报消息的 msgId |
| success | boolean | 是 | 是否成功接收 |
| record_id | number | 否 | 服务器存储的记录 ID（成功时） |
| error | string | 否 | 错误信息（失败时） |

### 4. 心跳消息

客户端定期发送心跳保持连接：

```json
{ "type": "ping" }
```

服务端响应：

```json
{ "type": "pong", "timestamp": 1704614400000 }
```

| 链路类型 | 心跳间隔 | 说明 |
|---------|---------|------|
| 本地链路（前端→后端） | 30 秒 | 网络稳定，较长间隔减少开销 |
| 远程链路（后端→远程服务器） | 10 秒 | 公网环境，较短间隔更快检测断开（不可配置） |

### 5. 错误消息 (error)

服务端发送的错误通知。

```json
{
    "type": "error",
    "error": "connection_limit_reached",
    "message": "连接数已达上限 (10)"
}
```

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| type | string | 是 | 固定为 `"error"` |
| error | string | 是 | 错误代码 |
| message | string | 是 | 错误描述 |
| code | number | 否 | HTTP 状态码（如 401） |

**常见错误代码：**

| 错误代码 | 说明 |
|---------|------|
| `connection_limit_reached` | 本地连接数已达上限（默认 10），服务端同时以 WebSocket 关闭码 `1013` 断开连接 |
| `device_offline` | 设备离线（仅远程模式，由远程服务器返回） |

**认证失败（401）**使用 `code` 字段标识：

```json
{
    "type": "error",
    "code": 401,
    "message": "Token 无效或已过期"
}
```

**设备离线响应**（远程模式下请求未在线的设备）：

```json
{
    "type": "response",
    "msg_id": "xxx",
    "success": false,
    "error": "设备离线"
}
```

***

## 灶台状态机

### 状态枚举

| 状态值 | 中文 | 说明 |
|-------|------|------|
| `"idle"` | 空闲 | 未开火 |
| `"active_with_person"` | 有人动火 | 开火且检测到人员 |
| `"active_no_person"` | 无人动火 | 开火但未检测到人员，开始计时 |
| `"warning"` | 预警中 | 无人看管超过 `warning_time` 秒 |
| `"alarm"` | 报警中 | 无人看管超过 `alarm_time` 秒 |
| `"cutoff"` | 已切电 | 无人看管超过 `action_time` 秒，已执行切电 |
| `"temp_alarm"` | 温度报警 | 温度超过阈值且无人看管 |

### 状态转换规则

```
                         ┌──────────────────────────────────────┐
                         │          温度>阈值 且 无人            │
                         │      (任意状态 → temp_alarm)          │
                         ▼                                      │
┌──────┐  开火+有人  ┌──────────────────┐                       │
│ IDLE │ ─────────→ │ active_with_person │                       │
│      │ ←───────── │                  │                       │
└──────┘  灭火      └──────────────────┘                       │
   ▲                       │                                    │
   │                  开火+无人                                  │
   │                       ▼                                    │
   │               ┌──────────────────┐                        │
   │               │ active_no_person │                        │
   │               │  (计时开始)       │                        │
   │               └──────────────────┘                        │
   │                       │                                    │
   │                 无人 ≥ warning_time                        │
   │                       ▼                                    │
   │               ┌──────────────────┐                        │
   │               │    WARNING       │                        │
   │               │    (预警)        │                        │
   │               └──────────────────┘                        │
   │                       │                                    │
   │                 无人 ≥ alarm_time                          │
   │                       ▼                                    │
   │               ┌──────────────────┐                        │
   │               │    ALARM         │                        │
   │               │    (报警)        │                        │
   │               └──────────────────┘                        │
   │                       │                                    │
   │                 无人 ≥ action_time                         │
   │                       ▼                                    │
   │               ┌──────────────────┐     人员回场 或         │
   │               │    CUTOFF        │ ── 电流恢复(10s后) ────→│
   │               │    (切电)        │                        │
   │               └──────────────────┘                        │
   │                                                           │
   │               ┌──────────────────┐     人员回场 或         │
   └────────────── │   TEMP_ALARM     │ ── 温度≤阈值 ─────────→┘
                   │   (温度报警)      │
                   └──────────────────┘
```

**优先级（从高到低）：**

1. **温度报警检测**：温度超过阈值且无人 → 立即进入 `temp_alarm`（优先级最高）
2. **温度报警恢复**：`temp_alarm` 状态下人员回场或温度降至阈值以下 → `idle`
3. **灭火**：`is_fire_on` 为 `false` → 立即回到 `idle`（硬件重置）
4. **切电恢复**：`cutoff` 状态下人员回场，或电流值超过阈值持续 10 秒 → `idle`
5. **有人动火**：`is_fire_on` 且 `has_person` → `active_with_person`
6. **无人动火计时**：`is_fire_on` 且无人员 → 按时间阈值逐级升级

### 倒计时字段说明

当灶台处于无人动火状态时，以下字段提供实时倒计时信息：

| 字段 | 说明 |
|------|------|
| `no_person_duration` | 无人看管持续时间（秒），精度 0.1 秒 |
| `warning_remaining` | 距预警剩余时间（秒），精度 0.1 秒，已触发时为 0 |
| `alarm_remaining` | 距报警剩余时间（秒），精度 0.1 秒，已触发时为 0 |
| `cutoff_remaining` | 距切电剩余时间（秒），精度 0.1 秒，已触发时为 0 |

### reset_zone 行为说明

`reset_zone` 仅在灶台处于 `WARNING` 或 `CUTOFF` 状态时有效，将状态重置为 `IDLE`。其他状态下调用不会报错，但返回 `"灶台不在预警或切电状态"` 的 message。

***

## 支持的 Action

### 灶台操作

#### get_zones — 获取所有灶台配置

**参数：** 无

**返回值：**

```json
[
    {
        "id": "zone_1",
        "name": "灶台1",
        "camera_id": "0",
        "roi": [[0.1, 0.1], [0.5, 0.1], [0.5, 0.5], [0.1, 0.5]],
        "enabled": true,
        "serial_index": 1,
        "fire_current_threshold": 100,
        "current_value": 0
    }
]
```

| 字段 | 类型 | 说明 |
|------|------|------|
| id | string | 灶台 ID |
| name | string | 灶台名称 |
| camera_id | string | 绑定的摄像头 ID |
| roi | array | 感兴趣区域坐标，归一化坐标 `[[x1,y1], [x2,y2], ...]`，值范围 0.0-1.0 |
| enabled | boolean | 是否启用 |
| serial_index | number | 电流检测分区索引 |
| fire_current_threshold | number | 火焰电流阈值 |
| current_value | number | 当前电流值（来自串口实时数据） |

#### get_zone — 获取单个灶台配置

**参数：**

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| zone_id | string | 是 | 灶台 ID |

**返回值：**

```json
{
    "id": "zone_1",
    "name": "灶台1",
    "camera_id": "0",
    "roi": [[0.1, 0.1], [0.5, 0.1], [0.5, 0.5], [0.1, 0.5]],
    "enabled": true
}
```

| 字段 | 类型 | 说明 |
|------|------|------|
| id | string | 灶台 ID |
| name | string | 灶台名称 |
| camera_id | string | 绑定的摄像头 ID |
| roi | array | 归一化坐标 |
| enabled | boolean | 是否启用 |

**错误：** 灶台不存在时返回 `success: false`，error 为 `"灶台 'xxx' 不存在"`。

#### create_zone — 创建灶台

**参数：**

| 字段 | 类型 | 必填 | 默认值 | 说明 |
|------|------|------|--------|------|
| name | string | 是 | — | 灶台名称 |
| camera_id | string | 是 | — | 摄像头 ID |
| roi | array | 否 | `[]` | 归一化坐标 `[[x,y], ...]`，值范围 0.0-1.0 |
| enabled | boolean | 否 | `true` | 是否启用 |
| serial_index | number | 否 | `0` | 电流检测分区索引 |
| fire_current_threshold | number | 否 | `100` | 火焰电流阈值 |
| enable_temp_sensor | boolean | 否 | `false` | 是否启用温度传感器。启用时自动分配传感器地址，**必须确保此时物理上只接入了一个传感器** |

**返回值：**

```json
{
    "id": "zone_1",
    "name": "灶台1",
    "camera_id": "0",
    "roi": [],
    "enabled": true,
    "serial_index": 0,
    "fire_current_threshold": 100,
    "temp_sensor_address": null,
    "temp_sensor_enabled": false
}
```

| 字段 | 类型 | 说明 |
|------|------|------|
| id | string | 自动生成的灶台 ID |
| name | string | 灶台名称 |
| camera_id | string | 摄像头 ID |
| roi | array | 归一化坐标 |
| enabled | boolean | 是否启用 |
| serial_index | number | 电流检测分区索引 |
| fire_current_threshold | number | 火焰电流阈值 |
| temp_sensor_address | number\|null | 温度传感器地址，未启用时为 `null` |
| temp_sensor_enabled | boolean | 温度传感器是否启用 |

**错误：**
- 摄像头不存在：`"摄像头 'xxx' 不存在"`
- 传感器地址耗尽：`"无可用的传感器地址，请先解绑其他传感器"`
- 串口未连接：`"发送传感器配置命令失败，串口可能未连接"`

#### update_zone — 更新灶台

**参数：**

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| zone_id | string | 是 | 灶台 ID |
| name | string | 否 | 灶台名称 |
| camera_id | string | 否 | 摄像头 ID |
| roi | array | 否 | 归一化坐标 |
| enabled | string | 否 | 设为 `false` 时会强制重置灶台状态为 `idle` |
| serial_index | number | 否 | 电流检测分区索引 |
| fire_current_threshold | number | 否 | 火焰电流阈值 |
| enable_temp_sensor | boolean | 否 | `true` 自动分配传感器地址，`false` 解绑传感器 |

**返回值：**

```json
{
    "id": "zone_1",
    "message": "更新成功"
}
```

**错误：** 灶台不存在、传感器地址耗尽、串口未连接等。

#### delete_zone — 删除灶台

**参数：**

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| zone_id | string | 是 | 灶台 ID |

**返回值：**

```json
{
    "id": "zone_1",
    "name": "灶台1",
    "message": "删除成功"
}
```

#### reset_zone — 重置灶台状态

仅在灶台处于 `WARNING` 或 `CUTOFF` 状态时有效。

**参数：**

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| zone_id | string | 是 | 灶台 ID |

**返回值：**

```json
{
    "id": "zone_1",
    "message": "灶台已重置"
}
```

#### toggle_fire — 模拟火焰开关

用于调试，手动模拟火焰开/关状态。

**参数：**

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| zone_id | string | 是 | 灶台 ID |
| is_on | boolean | 是 | `true` 模拟开火，`false` 模拟灭火 |

**返回值：**

```json
{
    "zone_id": "zone_1",
    "is_on": true,
    "message": "火焰状态已更新"
}
```

***

### 摄像头操作

#### get_cameras — 获取所有摄像头

**参数：** 无

**返回值：**

```json
[
    {
        "id": "0",
        "name": "前厅摄像头",
        "type": "rtsp",
        "status": "online",
        "width": 640,
        "height": 480,
        "fps": 30,
        "device": null,
        "rtsp_url": "rtsp://192.168.1.100:554/stream",
        "username": "admin",
        "password": "******"
    }
]
```

| 字段 | 类型 | 说明 |
|------|------|------|
| id | string | 摄像头 ID |
| name | string | 摄像头名称 |
| type | string | `"usb"` 或 `"rtsp"` |
| status | string | 状态枚举，见下方 |
| width | number | 分辨率宽度 |
| height | number | 分辨率高度 |
| fps | number | 帧率 |
| device | number\|null | USB 设备索引（USB 类型时有值） |
| rtsp_url | string\|null | RTSP 地址（RTSP 类型时有值） |
| username | string\|null | RTSP 用户名 |
| password | string\|null | RTSP 密码 |

**摄像头状态枚举（`status`）：**

| 值 | 说明 |
|----|------|
| `"offline"` | 离线 |
| `"connecting"` | 正在连接 |
| `"online"` | 在线 |
| `"error"` | 错误 |

#### get_camera — 获取单个摄像头

**参数：**

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| camera_id | string | 是 | 摄像头 ID |

**返回值：** 与 `get_cameras` 数组元素相同，但不包含 `username` 和 `password`。

#### create_camera — 创建摄像头

**参数：**

| 字段 | 类型 | 必填 | 默认值 | 说明 |
|------|------|------|--------|------|
| name | string | 是 | — | 摄像头名称 |
| type | string | 否 | `"rtsp"` | `"usb"` 或 `"rtsp"` |
| id | string | 否 | 自增 | 唯一 ID，不填时从 0 开始自增 |
| device | number | USB 必填 | — | USB 设备索引 |
| rtsp_url | string | RTSP 必填 | — | RTSP 地址 |
| username | string | 否 | — | RTSP 用户名 |
| password | string | 否 | — | RTSP 密码 |
| width | number | 否 | `640` | 分辨率宽度 |
| height | number | 否 | `480` | 分辨率高度 |
| fps | number | 否 | `30` | 帧率 |

**返回值：**

```json
{
    "id": "0",
    "name": "前厅摄像头",
    "status": "connecting"
}
```

#### update_camera — 更新摄像头

更新时会先移除旧摄像头实例再创建新实例。

**参数：** 与 `create_camera` 相同，`camera_id`（或 `id`）为必填。

**返回值：**

```json
{
    "id": "0",
    "message": "更新成功"
}
```

#### delete_camera — 删除摄像头

**参数：**

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| camera_id | string | 是 | 摄像头 ID |

**返回值：**

```json
{
    "id": "0",
    "message": "删除成功"
}
```

#### get_usb_devices — 获取 USB 设备列表

**参数：** 无

**返回值：** USB 摄像头设备列表（格式取决于系统实现）。

#### preview_camera — 获取摄像头预览

获取摄像头当前帧的 Base64 编码图片。如果帧缓冲区为空，最多等待 2 秒。

**参数：**

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| camera_id | string | 是 | 摄像头 ID |

**返回值：**

```json
{
    "image": "data:image/jpeg;base64,/9j/4AAQSkZJRg..."
}
```

| 字段 | 类型 | 说明 |
|------|------|------|
| image | string | JPEG 格式的 Base64 图片，带 `data:image/jpeg;base64,` 前缀 |

**错误：**
- 摄像头不存在
- 摄像头离线（会尝试自动重连）
- 帧获取超时（`"获取预览失败，摄像头可能正在初始化"`）

***

### 状态和设置

#### get_status — 获取所有灶台状态（核心接口）

**参数：** 无

**返回值：**

```json
[
    {
        "id": "zone_1",
        "name": "灶台1",
        "camera_id": "0",
        "roi": [[0.1, 0.1], [0.5, 0.1], [0.5, 0.5], [0.1, 0.5]],
        "enabled": true,
        "state": "active_no_person",
        "is_fire_on": true,
        "has_person": false,
        "no_person_duration": 45.3,
        "warning_remaining": 44.7,
        "alarm_remaining": 134.7,
        "cutoff_remaining": 254.7,
        "current_value": 120,
        "temperature": 28.5,
        "last_snapshot_path": null
    }
]
```

| 字段 | 类型 | 说明 |
|------|------|------|
| id | string | 灶台 ID |
| name | string | 灶台名称 |
| camera_id | string | 绑定的摄像头 ID |
| roi | array | 归一化坐标 |
| enabled | boolean | 是否启用 |
| state | string | 当前状态，见灶台状态机章节 |
| is_fire_on | boolean | 是否检测到火焰（来自电流检测） |
| has_person | boolean | 是否检测到人员（来自视觉检测） |
| no_person_duration | number | 无人看管持续时间（秒），精度 0.1 |
| warning_remaining | number | 距预警剩余秒数，精度 0.1，已触发或不适用时为 `0.0` |
| alarm_remaining | number | 距报警剩余秒数，精度 0.1，已触发或不适用时为 `0.0` |
| cutoff_remaining | number | 距切电剩余秒数，精度 0.1，已触发或不适用时为 `0.0` |
| current_value | number | 当前电流值（来自串口实时数据，默认 `0`） |
| temperature | number | 当前温度（摄氏度，精度 0.1，默认 `0.0`） |
| last_snapshot_path | string\|null | 最近一次告警截图的本地文件路径 |

#### get_device — 获取设备信息

**参数：** 无

**返回值：**

```json
{
    "name": "动火离人安全监测系统",
    "version": "0.1.0",
    "device_id": "DHLR001",
    "platform": "Linux",
    "python_version": "3.10.0",
    "zone_mode": "zoned"
}
```

| 字段 | 类型 | 说明 |
|------|------|------|
| name | string | 系统名称 |
| version | string | 系统版本号 |
| device_id | string | 设备 ID |
| platform | string | 操作系统（`"Linux"`、`"Windows"`、`"Darwin"`） |
| python_version | string | Python 版本 |
| zone_mode | string | 当前监测模式：`"zoned"` 或 `"single"` |

#### get_performance — 获取性能指标

**参数：** 无

**返回值：**

```json
{
    "engine": "pytorch",
    "model": "models/yolov8n.pt",
    "inference_time_ms": 45.23,
    "avg_inference_time_ms": 48.56,
    "min_inference_time_ms": 32.10,
    "max_inference_time_ms": 120.45,
    "fps": 20.6,
    "cpu_percent": 35.2,
    "memory_mb": 256.8,
    "npu_load": 0.0,
    "sample_count": 87
}
```

| 字段 | 类型 | 说明 |
|------|------|------|
| engine | string | 推理引擎：`"pytorch"` 或 `"rknn"` |
| model | string | 模型文件路径 |
| inference_time_ms | number | 最近一次推理耗时（毫秒） |
| avg_inference_time_ms | number | 平均推理耗时（毫秒） |
| min_inference_time_ms | number | 最短推理耗时（毫秒） |
| max_inference_time_ms | number | 最长推理耗时（毫秒） |
| fps | number | 推理帧率（`1000 / avg_inference_time_ms`） |
| cpu_percent | number | CPU 使用率（%） |
| memory_mb | number | 内存使用量（MB，RSS） |
| npu_load | number | NPU 负载（%，不可用时为 `0`） |
| sample_count | number | 推理时间采样数（滑动窗口，最多 100） |

#### get_snapshot — 获取告警快照

**参数：**

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| filename | string | 是 | 快照文件名（相对于 `snapshots/` 目录） |

**返回值：**

```json
{
    "image": "data:image/jpeg;base64,/9j/4AAQSkZJRg...",
    "filename": "zone_1_20240107_120000.jpg"
}
```

| 字段 | 类型 | 说明 |
|------|------|------|
| image | string | Base64 编码图片，MIME 类型根据文件扩展名确定（`.jpg`/`.jpeg` → `image/jpeg`，`.png` → `image/png`） |
| filename | string | 文件名 |

#### get_settings — 获取系统设置

**参数：**

| 字段 | 类型 | 必填 | 默认值 | 说明 |
|------|------|------|--------|------|
| category | string | 否 | `"all"` | `"all"`、`"alarm"`、`"system"`、`"voice"` |

**返回值（`category: "all"` 时）：**

```json
{
    "alarm": {
        "warning_time": 90,
        "alarm_time": 180,
        "action_time": 300,
        "broadcast_interval": 10,
        "warning_message": "请注意，灶台无人看管",
        "alarm_message": "警告，灶台长时间无人看管",
        "action_message": "紧急，灶台已切电",
        "temp_alarm_threshold": 60.0,
        "temp_alarm_message": "温度报警，灶台温度过高"
    },
    "system": {
        "name": "动火离人安全监测系统",
        "version": "0.1.0",
        "device_id": "DHLR001",
        "debug": false,
        "zone_mode": "zoned"
    },
    "voice": {
        "enabled": true,
        "volume": 0.8
    }
}
```

**alarm 子对象字段：**

| 字段 | 类型 | 说明 |
|------|------|------|
| warning_time | number | 预警时间阈值（秒） |
| alarm_time | number | 报警时间阈值（秒） |
| action_time | number | 切电时间阈值（秒） |
| broadcast_interval | number | 语音播报间隔（秒） |
| warning_message | string | 预警语音文案 |
| alarm_message | string | 报警语音文案 |
| action_message | string | 切电语音文案 |
| temp_alarm_threshold | number | 温度报警阈值（摄氏度） |
| temp_alarm_message | string | 温度报警语音文案 |

#### update_settings — 更新系统设置

**参数：**

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| category | string | 是 | 目前仅支持 `"alarm"`，其他值静默忽略 |
| settings | object | 是 | 要更新的字段，仅传需要修改的字段 |

**可更新的 alarm 字段：** `warning_time`、`alarm_time`、`action_time`、`broadcast_interval`、`warning_message`、`alarm_message`、`action_message`、`temp_alarm_threshold`（number）、`temp_alarm_message`

**返回值：**

```json
{
    "message": "设置已更新"
}
```

> **注意：** `temp_alarm_threshold` 内部会转换为 `float` 类型。

#### set_device_id — 设置设备 ID

**参数：**

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| device_id | string | 是 | 新的设备 ID（去除首尾空格后不能为空） |

**返回值：**

```json
{
    "device_id": "DHLR001",
    "message": "设备ID已更新"
}
```

#### get_network — 获取网络状态

**参数：** 无

**返回值：** 与 `network_interface` 事件的 `data` 字段相同。

#### get_remote_config — 获取远程配置

**参数：** 无

**返回值：**

```json
{
    "enabled": true,
    "server_url": "https://vis.example.com",
    "websocket_path": "/ws/dhlr/device/",
    "login_path": "/login",
    "username": "admin",
    "has_token": true,
    "is_connected": true,
    "is_connecting": false,
    "last_error": "",
    "reconnect_attempts": 0
}
```

| 字段 | 类型 | 说明 |
|------|------|------|
| enabled | boolean | 远程连接是否启用 |
| server_url | string | 服务器地址 |
| websocket_path | string | WebSocket 路径 |
| login_path | string | 登录接口路径 |
| username | string | 登录用户名 |
| has_token | boolean | 是否已持有 Token |
| is_connected | boolean | 当前是否已连接 |
| is_connecting | boolean | 是否正在连接中 |
| last_error | string | 最近一次连接错误信息 |
| reconnect_attempts | number | 已重连次数 |

#### update_remote_config — 更新远程配置

**参数：**

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| enabled | boolean | 否 | 是否启用远程连接 |
| server_url | string | 否 | 服务器地址 |
| websocket_path | string | 否 | WebSocket 路径 |
| login_path | string | 否 | 登录接口路径 |
| username | string | 否 | 登录用户名 |
| password | string | 否 | 登录密码。**更新密码时会自动清除已有 Token** |

**副作用：** 当 `enabled` 为 `true` 时，会断开当前远程连接并异步重新建立连接。

**返回值：**

```json
{
    "message": "远程配置已更新"
}
```

#### verify_remote_login — 校验远程登录

**参数：**

| 字段 | 类型 | 必填 | 默认值 | 说明 |
|------|------|------|--------|------|
| server_url | string | 是 | — | 服务器地址 |
| login_path | string | 否 | `"/login"` | 登录接口路径 |
| username | string | 是 | — | 用户名 |
| password | string | 是 | — | 密码 |

**返回值：**

```json
{
    "success": true,
    "message": "校验成功"
}
```

校验成功时会自动保存服务器地址、登录路径、用户名、密码和 Token 到配置。

***

### 语音音量

#### get_volume — 获取当前语音音量

**参数：** 无

**返回值：**

```json
{
    "volume": 0.8
}
```

#### set_volume — 设置语音音量

**参数：**

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| volume | number | 是 | 音量值，范围 `0.0`（静音）到 `1.0`（最大音量） |

**返回值：**

```json
{
    "volume": 0.8,
    "message": "音量已更新"
}
```

设置后立即生效并持久化到配置文件。

***

### 监测模式

#### get_zone_mode — 获取当前监测模式

**参数：** 无

**返回值：**

```json
{
    "zone_mode": "zoned",
    "zone_count": 3
}
```

| 字段 | 类型 | 说明 |
|------|------|------|
| zone_mode | string | `"zoned"`（分区监测）或 `"single"`（不分区监测） |
| zone_count | number | 当前灶台数量 |

#### set_zone_mode — 设置监测模式

**参数：**

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| zone_mode | string | 是 | `"zoned"` 或 `"single"` |

**返回值：**

```json
{
    "zone_mode": "single",
    "message": "已切换到不分区监测模式"
}
```

切换时允许保留现有灶台配置，无需预先删除。不分区模式下使用灶台配置中的 `serial_index` 和 `fire_current_threshold`。

***

### 日志

#### get_log_files — 获取日志文件列表

**参数：** 无

**返回值：**

```json
[
    {
        "name": "2024-01-07.log",
        "size": 102400,
        "mtime": 1704614400.0
    }
]
```

| 字段 | 类型 | 说明 |
|------|------|------|
| name | string | 日志文件名 |
| size | number | 文件大小（**字节**） |
| mtime | number | 最后修改时间（**Unix 秒级时间戳**，注意：非毫秒） |

按文件名倒序排列（最新在前）。

#### get_log_content — 读取日志内容

支持分页读取，从文件**末尾**倒数分页（第 1 页为最新的日志）。

**参数：**

| 字段 | 类型 | 必填 | 默认值 | 说明 |
|------|------|------|--------|------|
| filename | string | 否 | 最新文件 | 日志文件名，不提供时读取最新的日志文件 |
| page | number | 否 | `1` | 页码，从 1 开始（第 1 页为最新日志） |
| page_size | number | 否 | `500` | 每页行数，范围 100-1000 |

**返回值：**

```json
{
    "filename": "2024-01-07.log",
    "content": "2024-01-07 12:00:00 [INFO] ...\n...",
    "total_lines": 1500,
    "page": 1,
    "page_size": 500,
    "total_pages": 3
}
```

| 字段 | 类型 | 说明 |
|------|------|------|
| filename | string | 实际读取的文件名 |
| content | string | 当前页的日志内容 |
| total_lines | number | 文件总行数 |
| page | number | 当前页码 |
| page_size | number | 每页行数 |
| total_pages | number | 总页数 |

***

### 串口通讯

#### get_serial_ports — 获取系统可用的串口列表

**参数：** 无

**返回值：**

```json
[
    {
        "device": "COM3",
        "name": "COM3",
        "description": "\\Device\\Serial0",
        "hwid": ""
    }
]
```

| 字段 | 类型 | 说明 |
|------|------|------|
| device | string | 串口设备路径（如 `COM3`、`/dev/ttyUSB0`） |
| name | string | 串口名称 |
| description | string | 设备描述 |
| hwid | string | 硬件 ID（通常为空） |

#### get_serial_config — 获取串口配置

**参数：** 无

**返回值：**

```json
{
    "enabled": true,
    "port": "COM3",
    "baudrate": 9600,
    "poll_interval": 1.0,
    "is_open": true,
    "debug_hex": false
}
```

| 字段 | 类型 | 说明 |
|------|------|------|
| enabled | boolean | 串口是否启用 |
| port | string | 串口端口 |
| baudrate | number | 波特率 |
| poll_interval | number | 轮询间隔（秒） |
| is_open | boolean | 串口是否已打开 |
| debug_hex | boolean | 是否开启 16 进制调试日志 |

#### update_serial_config — 更新串口配置

**参数：** 所有字段均可选。

| 字段 | 类型 | 说明 |
|------|------|------|
| enabled | boolean | 是否启用 |
| port | string | 串口端口 |
| baudrate | number | 波特率 |
| poll_interval | number | 轮询间隔（秒） |

**返回值：**

```json
{
    "message": "串口配置已更新"
}
```

#### get_currents — 获取所有分区电流值

**参数：** 无

**返回值：**

```json
{
    "currents": {
        "zone_1": 120,
        "zone_2": 0
    }
}
```

串口异常时可能返回 `{ "currents": {}, "error": "错误信息" }`。

#### get_lora_config — 获取 LoRa 配置

**参数：** 无

**返回值：**

```json
{
    "id": 1,
    "channel": 23
}
```

串口异常时可能返回 `{ "id": 0, "channel": 0, "error": "错误信息" }`。

#### set_lora_config — 设置 LoRa 配置

**参数：** 至少提供一个。

| 字段 | 类型 | 说明 |
|------|------|------|
| id | number\|string | LoRa 编号（接受数字或字符串，内部转为 int） |
| channel | number\|string | LoRa 信道（接受数字或字符串，内部转为 int） |

**返回值：**

```json
{
    "message": "LoRa配置已更新: 编号已设置为 1, 信道已设置为 23"
}
```

#### set_serial_debug — 设置串口调试日志开关

**参数：**

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| enabled | boolean | 是 | 开启或关闭 16 进制调试日志 |

**返回值：**

```json
{
    "enabled": true,
    "message": "串口16进制调试日志已开启"
}
```

开启后串口收发数据以 16 进制格式打印到日志：`[TX] FF AA FF 01 03 ...`（发送）、`[RX] 01 03 02 00 05 ...`（接收）。

***

### GPIO 控制

#### get_gpio_pins — 获取可用 GPIO 引脚列表

**参数：** 无

**返回值：**

```json
{
    "pins": ["gpio0", "gpio1", "gpio2"]
}
```

#### get_gpio_config — 获取 GPIO 配置

**参数：** 无

**返回值：**

```json
{
    "enabled": true,
    "gpio_path": "/sys/external_gpio",
    "pin_fire": "gpio0",
    "pin_absence": "gpio1",
    "pin_alarm": "gpio2",
    "estop_enabled": true,
    "pin_estop": "gpio10",
    "estop_active_low": true
}
```

| 字段 | 类型 | 说明 |
|------|------|------|
| enabled | boolean | GPIO 是否启用 |
| gpio_path | string | GPIO sysfs 路径 |
| pin_fire | string | 动火指示灯引脚 |
| pin_absence | string | 离人指示灯引脚 |
| pin_alarm | string | 报警指示灯引脚 |
| estop_enabled | boolean | 是否启用急停输入监听 |
| pin_estop | string | 急停按钮输入引脚 |
| estop_active_low | boolean | 急停有效电平：`true`=低电平有效（按下接地触发） |

#### update_gpio_config — 更新 GPIO 配置

**参数：** 所有字段均可选。

| 字段 | 类型 | 说明 |
|------|------|------|
| enabled | boolean | 是否启用指示灯输出 |
| pin_fire | string | 动火指示灯引脚名（如 `"gpio0"`） |
| pin_absence | string | 离人指示灯引脚名 |
| pin_alarm | string | 报警指示灯引脚名 |
| estop_enabled | boolean | 是否启用急停输入监听 |
| pin_estop | string | 急停按钮输入引脚名（如 `"gpio10"`） |
| estop_active_low | boolean | 急停有效电平：`true`=低电平有效 |

**返回值：**

```json
{
    "message": "GPIO 配置已更新"
}
```

只要有任意灶台处于对应状态（动火/离人/报警），相应指示灯就会亮起。

***

### USB OTG 模式

#### get_usb_otg_mode — 获取当前 USB OTG 模式

**参数：** 无

**返回值：**

```json
{
    "mode": "host"
}
```

| 字段 | 类型 | 说明 |
|------|------|------|
| mode | string | 当前模式：`"host"`（主设备）或 `"peripheral"`（从设备） |

**错误：**
- 设备不支持：`"当前设备不支持 USB OTG 模式切换"`
- 权限不足：`"无权限读取 USB OTG 模式，请以 root 用户运行"`

#### set_usb_otg_mode — 设置 USB OTG 模式

通过 `sudo -S` 提权写入 `/sys/devices/platform/fe8a0000.usb2-phy/otg_mode` 切换 USB OTG 工作模式。

**参数：**

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| mode | string | 是 | `"host"`（主设备，可接 U 盘、鼠标等）或 `"peripheral"`（从设备，连接电脑时被识别为 gadget） |

**返回值：**

```json
{
    "mode": "peripheral",
    "message": "已切换为 peripheral 模式"
}
```

**错误：**
- 参数无效：`"无效的 USB OTG 模式，仅支持 'host' 或 'peripheral'"`
- 设备不支持：`"当前设备不支持 USB OTG 模式切换"`
- 权限不足：`"无权限设置 USB OTG 模式，请以 root 用户运行"`

***

### 巡检操作

> **异步执行说明：** `start_patrol`、`patrol_self_check`、`patrol_alarm_demo`、`patrol_force_warning`、`patrol_force_alarm`、`patrol_force_cutoff` 均在后台线程中异步执行。调用后立即返回 `{success: true, message: "..."}`，实际执行进度通过 `patrol_event` 推送。`patrol_cutoff_zone` 也会同步上报报警记录到远程服务器。

#### start_patrol — 开始巡检模式

**参数：** 无

**返回值：**

```json
{
    "success": true,
    "message": "巡检模式已启动"
}
```

#### stop_patrol — 退出巡检模式

**参数：** 无

**返回值：**

```json
{
    "success": true,
    "message": "巡检模式已退出"
}
```

#### get_patrol_status — 获取巡检状态

**参数：** 无

**返回值：**

```json
{
    "is_active": false,
    "current_step": "idle",
    "progress": 0,
    "message": "",
    "results": []
}
```

| 字段 | 类型 | 说明 |
|------|------|------|
| is_active | boolean | 巡检是否正在进行 |
| current_step | string | 当前步骤，见巡检步骤枚举 |
| progress | number | 进度百分比 0-100 |
| message | string | 当前步骤描述 |
| results | array | 巡检结果列表（最多 20 条） |

#### patrol_check_person — 检测单个灶台的离人状态

**参数：**

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| zone_id | string | 是 | 灶台 ID |

**返回值：**

```json
{
    "success": true,
    "has_person": true,
    "message": "灶台1 检测到有人"
}
```

#### patrol_check_fire — 检测单个灶台的动火状态

**参数：**

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| zone_id | string | 是 | 灶台 ID |

**返回值：**

```json
{
    "success": true,
    "is_fire_on": true,
    "message": "灶台1 检测到动火"
}
```

#### patrol_alarm_demo — 报警演示

执行完整的三阶段报警演示：预警 → 报警 → 切电。单灶台模式（传 `zone_id`）每步间隔 10 秒；自动选择模式（不传 `zone_id`）每步间隔 5 秒。

**参数：**

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| zone_id | string | 否 | 灶台 ID。不传时自动选择第一个动火灶台 |

**返回值：**

```json
{
    "success": true,
    "message": "报警演示已启动"
}
```

**前提：** 执行报警演示的灶台需要处于动火状态。

#### patrol_cutoff_zone — 单灶台切电

**参数：**

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| zone_id | string | 是 | 灶台 ID |

**返回值：**

```json
{
    "success": true,
    "message": "灶台1 已切电"
}
```

执行后会同步上报报警记录到远程服务器。

#### patrol_self_check — 设备自检

批量检测所有灶台的离人和动火状态。

**参数：** 无

**返回值：**

```json
{
    "success": true,
    "message": "设备自检已启动"
}
```

异步执行，进度通过 `patrol_event` 推送。

#### patrol_force_warning — 强制预警（所有区）

**参数：** 无

**返回值：**

```json
{
    "success": true,
    "message": "强制预警已触发"
}
```

异步执行，每个灶台间隔 2 秒处理，同时上报报警记录到远程服务器。

#### patrol_force_alarm — 强制报警（所有区）

**参数：** 无

**返回值：**

```json
{
    "success": true,
    "message": "强制报警已触发"
}
```

#### patrol_force_cutoff — 强制切电（所有区）

**参数：** 无

**返回值：**

```json
{
    "success": true,
    "message": "强制切电已触发"
}
```

***

### 系统更新

#### trigger_update — 触发系统更新

执行 `git fetch origin` + `git reset --hard origin/{branch}` 强制更新到远程最新代码，然后执行 `sudo systemctl restart ai-dhlr` 重启服务。等待 1 秒后开始重启。

**参数：** 无

**返回值：**

```json
{
    "success": true,
    "message": "已更新到 origin/main，服务正在重启..."
}
```

执行后 WebSocket 连接会断开，需要等待服务重启后重新连接。建议在调用前向用户显示确认对话框。

### 依赖安装

#### install_dependencies — 安装/更新依赖

执行 `pip install -r requirements.txt`，超时时间 5 分钟（300 秒）。

**参数：** 无

**返回值：**

```json
{
    "success": true,
    "message": "依赖安装成功",
    "output": "Collecting fastapi..."
}
```

| 字段 | 类型 | 说明 |
|------|------|------|
| success | boolean | 是否成功 |
| message | string | 结果描述 |
| output | string | pip 命令输出（截断到 2000 字符，仅成功时有值） |

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

## 消息路由规则

### 本地消息路由

```
本地客户端发送消息
    │
    ├─ type == "request" → 本地处理，响应仅返回给发送者（target 无效）
    │
    └─ 其他类型
        ├─ target == "remote" → 仅转发到远程服务器
        ├─ target == "all"   → 本地广播 + 转发到远程服务器（默认）
        └─ 其他值            → 仅本地广播
```

### 远程消息路由

```
远程服务器发送消息 → 广播到所有本地客户端（target 字段被忽略）
```

***

## 连接生命周期

### 重连策略（指数退避）

| 重连次数 | 延迟时间 |
|---------|---------|
| 1 | 1 秒 |
| 2 | 2 秒 |
| 3 | 4 秒 |
| 4+ | 最大 30 秒 |

### 本地连接限制

最大并发连接数为 10（针对 2GB 内存的边缘设备优化）。超出时服务端发送 `connection_limit_reached` 错误并以 WebSocket 关闭码 `1013`（Try Again Later）断开连接。

### Token 失效自动恢复

1. 设备收到 `code: 401` 错误消息
2. 清除本地 Token
3. 重新调用登录接口获取新 Token
4. 使用新 Token 重新建立 WebSocket 连接

***

## 时间戳格式约定

| 场景 | 格式 | 示例 |
|------|------|------|
| WebSocket 消息中的 timestamp | 毫秒级 Unix 时间戳 | `1704614400000` |
| 日志文件 mtime（`get_log_files`） | 秒级 Unix 时间戳（float） | `1704614400.0` |

> **注意：** 除日志文件的 `mtime` 字段外，协议中所有时间戳均为毫秒级。
