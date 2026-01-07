# WebSocket 通讯协议规范

## 概述

动火离人安全监测系统使用 WebSocket 实现全双工实时通讯。所有 API 调用都通过 WebSocket 消息进行，系统支持两种链路：

- **本地链路**：设备本地 Web 端 ↔ Python 后端（`ws://localhost:8000/ws/status`）
- **远程链路**：Python 后端 ↔ 远程服务器（`wss://{server}/{path}`）

**两种链路共用同一套协议**，便于统一开发和维护。

---

## 连接地址

| 链路类型 | 地址格式 | 示例 |
|---------|---------|------|
| 本地链路 | `ws://localhost:{port}/ws/status` | `ws://localhost:8000/ws/status` |
| 远程链路 | `ws(s)://{server}/{path}` | `wss://vis.example.com/dhlr/socket` |

远程链路需要在请求头中携带 Token：
```http
Authorization: Bearer eyJhbGciOiJIUzUxMiJ9...
```

---

## 消息类型

### 1. 请求消息 (request)

客户端发起的请求，期待服务端返回对应的响应。

```json
{
    "type": "request",
    "msg_id": "msg_1704614400000_1",
    "action": "get_zones",
    "params": {}
}
```

| 字段 | 类型 | 必填 | 说明 |
|-----|------|-----|------|
| type | string | 是 | 固定为 "request" |
| msg_id | string | 是 | 消息唯一标识，用于匹配响应 |
| action | string | 是 | 请求的操作类型 |
| params | object | 否 | 操作参数 |

---

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

| 字段 | 类型 | 必填 | 说明 |
|-----|------|-----|------|
| type | string | 是 | 固定为 "response" |
| msg_id | string | 是 | 对应请求的 msg_id |
| success | boolean | 是 | 操作是否成功 |
| data | any | 否 | 返回的数据 |
| error | string | 否 | 错误信息（失败时） |

---

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

| 字段 | 类型 | 必填 | 说明 |
|-----|------|-----|------|
| type | string | 是 | 固定为 "alarm_event" |
| data | object | 是 | 报警事件数据 |
| data.zone_id | string | 是 | 灶台ID |
| data.zone_name | string | 是 | 灶台名称 |
| data.alarm_type | string | 是 | 报警类型：warning（预警）、alarm（报警）、cutoff（切电） |
| data.image | string | 否 | 抓拍图片Base64编码（JPEG格式），格式为 `data:image/jpeg;base64,...` |
| data.message | string | 否 | 事件消息描述 |

**网络状态**
```json
{
    "type": "network_status",
    "data": {
        "local_connected": true,
        "remote_connected": false,
        "remote_server": "wss://vis.example.com/dhlr/socket"
    }
}
```

---

### 4. 心跳消息

客户端定期发送心跳保持连接：

```json
{ "type": "ping" }
```

服务端响应：

```json
{ "type": "pong", "timestamp": 1704614400000 }
```

---

## 支持的 Action

### 灶台操作

| Action | 说明 | 参数 |
|--------|------|------|
| `get_zones` | 获取所有灶台配置 | 无 |
| `get_zone` | 获取单个灶台 | `zone_id` |
| `create_zone` | 创建灶台 | `name`, `camera_id`, `roi?`, `enabled?` |
| `update_zone` | 更新灶台 | `zone_id`, `name?`, `camera_id?`, `roi?`, `enabled?` |
| `delete_zone` | 删除灶台 | `zone_id` |
| `reset_zone` | 重置灶台状态 | `zone_id` |
| `toggle_fire` | 模拟火焰开关 | `zone_id`, `is_on` |

### 摄像头操作

| Action | 说明 | 参数 |
|--------|------|------|
| `get_cameras` | 获取所有摄像头 | 无 |
| `get_camera` | 获取单个摄像头 | `camera_id` |
| `create_camera` | 创建摄像头 | `id`, `name`, `type`, ... |
| `update_camera` | 更新摄像头 | `camera_id`, ... |
| `delete_camera` | 删除摄像头 | `camera_id` |
| `get_usb_devices` | 获取 USB 设备列表 | 无 |
| `preview_camera` | 获取摄像头预览 Base64 | `camera_id` |

### 状态和设置

| Action | 说明 | 参数 |
|--------|------|------|
| `get_status` | 获取所有灶台状态 | 无 |
| `get_device` | 获取设备信息 | 无 |
| `get_performance` | 获取性能指标 | 无 |
| `get_snapshot` | 获取告警快照 Base64 | `filename` |
| `get_settings` | 获取系统设置 | `category?` |
| `update_settings` | 更新系统设置 | `category`, `settings` |
| `get_network` | 获取网络状态 | 无 |
| `get_remote_config` | 获取远程配置 | 无 |
| `update_remote_config` | 更新远程配置 | `enabled`, `server_url`, ... |
| `verify_remote_login` | 校验远程登录 | `server_url`, `username`, `password` |

### 日志

| Action | 说明 | 参数 |
|--------|------|------|
| `get_log_files` | 获取日志文件列表 | 无 |
| `get_log_content` | 读取日志内容 | `filename?`, `lines?` |

---

## 重连策略

### 指数退避算法

| 重连次数 | 延迟时间 |
|---------|---------|
| 1 | 1秒 |
| 2 | 2秒 |
| 3 | 4秒 |
| 4+ | 最大 30秒 |

### Token 失效处理

收到 401 错误时自动调用 `/login` 获取新 Token 后重连。

---

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
