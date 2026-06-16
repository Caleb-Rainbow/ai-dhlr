# BLE 配网协议（蓝牙网关 ↔ 第一方边缘设备）

> 版本：proto `1`　·　两端（Android `BluetoothGateway` + 设备 `ai-dhlr-ble.service`）共实现此契约。
> 协议**完全设备无关**；设备特有差异（识别字段、UI 配置项）在 Android 侧 `DeviceProfile` 中描述，不在本协议内。

## 1. 角色

- **中心（Central / Client）**：Android App。扫描、连接、读 DEVICE_INFO、写 REQUEST、订阅 STATUS 通知。
- **外设（Peripheral / Server）**：边缘设备上的 `ai-dhlr-ble.service`（root，bless）。广播、暴露 GATT、处理配网命令（WiFi + 应用配置）。

## 2. GATT 服务与特征值

Service UUID（所有第一方设备共用）：
```
7d408e67-4b7d-8e1a-9b3c-2f1a00000001   Provisioning Service
```

| 特征值 UUID（末 4 位） | 名称 | 属性 | 载荷 | 说明 |
|---|---|---|---|---|
| `…00000002` | DEVICE_INFO | read | **裸 JSON（无分帧）** | 设备身份；连接后立即读，用于设备类型识别 |
| `…00000003` | REQUEST | write | 分帧 JSON | App→设备 的命令（scan_wifi / set_config / apply / cancel / get_config） |
| `…00000004` | STATUS | notify | 分帧 JSON | 设备→App 的状态/事件（scan_results / state_update / error） |
| `…00000005` | AUTH | （预留） | — | `// TODO(auth)`：开发期不使用；未来鉴权握手占位，**不改变本协议形状** |

> 广播：服务 UUID 作为 advertise 的 ServiceUUID，LocalName 形如 `AI-DHLR-<hwid 末 4 位>`。

## 3. DEVICE_INFO（裸 JSON，直读）

```json
{
  "type": "dhlr",
  "model": "ai-dhlr-rk3568",
  "fw": "0.2.0",
  "hwid": "dc-ec-4f-7e-78-58",
  "name": "AI动火离人",
  "proto": 1
}
```
- `type`：设备类型标识，与 Android `DeviceProfile.type` 匹配（`dhlr` / 未来其他）。
- `proto`：协议版本。App 读到后比对自身支持版本，不匹配 → 拒绝配网并提示（`ProtocolVersionMismatch`）。
- `hwid`：稳定硬件标识，用作已配网设备的持久化主键。
- 编码 UTF-8；通常 < 200B，单次 read 即可，故不分帧。

## 4. 分帧（REQUEST write 与 STATUS notify 共用）

REQUEST/STATUS 的 JSON 可能超过单个 BLE 包（如 `scan_results` 列表），统一分帧。

### 4.1 帧格式（大端）
```
偏移  长度  字段      说明
0     1     magic    固定 0xA5
1     2     seq      帧序号 u16（每帧递增，检测丢帧/乱序）
3     2     len      payload 字节数 u16
5     N     payload  JSON UTF-8（N = len）
5+N   2     crc16    CRC16-CCITT-FALSE，覆盖 magic..payload（不含 crc 自身）
```

### 4.2 CRC16
- 变体：**CRC-CCITT (0xFFFF)** / CRC16-CCITT-FALSE
- 多项式 `0x1021`，初值 `0xFFFF`，输入/输出不反转（reflect=false），xorout `0x0000`。
- 两端同实现；Android `Crc16` 与设备 `protocol.crc16` 必须对同一字节序列产生相同结果。

### 4.3 切块（按协商 MTU）
- 编码后的整帧字节流按 `(negotiatedMTU - 3)` 切块（3 = ATT 头开销）。
- **写侧（REQUEST）**：多块按序写入 REQUEST 特征值，**每块等 `onWrite` 成功回调后再发下一块**，严格不交错（避免 GATT 133/0x85 竞态）。
- **收侧**：按序累积块字节 → 解析（遇 `magic` 起帧，凑齐 `len`+2 校验 `crc16`）→ 校验通过解 JSON；CRC 错 → 设备回 `error{code:"bad_frame"}`，App 重发整帧。
- 单帧大多 < 一块（device_info、set_config 通常 < 244B）；`scan_results` 可能多块。

## 5. 命令（REQUEST 载荷 JSON）

所有命令对象：`{"cmd": "<name>", "id": <u32>, ...}`。`id` 由 App 生成，设备在对应 `state_update`/`error` 回引，便于匹配。

| cmd | 字段 | 说明 |
|---|---|---|
| `scan_wifi` | — | 触发设备 `nmcli` 扫描周边 WiFi，结果异步经 STATUS `scan_results` 回传 |
| `set_config` | `config: {wifi:{ssid,password}, remote:{...}, system:{...}}` | 下发完整配置（不立即生效） |
| `apply` | — | 应用上次 `set_config`：切 wlan0 STA + 调 dhlr 内部接口落 remote/system |
| `cancel` | — | 取消进行中的扫描/应用（尽力） |
| `get_config` | — | （可选）读取当前 remote/system，经 STATUS 回传，用于重新配网回填 |

`set_config.config` 示例：
```json
{
  "wifi":   { "ssid": "TP-LINK_XC", "password": "********" },
  "remote": { "enabled": true, "server_url": "https://vis.example.com", "token": "eyJ..." },
  "system": { "name": "1号厨房监测", "device_id": "C206B6AF476AEE73" }
}
```
> 字段子集允许：只下发变化的键；未下发键保持不变。App 端 `DeviceProfile.buildConfig` 决定哪些键。

## 6. 状态/事件（STATUS 载荷 JSON）

| event | 字段 | 说明 |
|---|---|---|
| `scan_results` | `id, networks:[{ssid,rssi,security}]` | 扫描结果（可能多帧） |
| `state_update` | `id, state, ip?, ssid?` | 配网状态机迁移（见 §7） |
| `error` | `id?, code, message?` | 错误（见 §8） |

`security` 取值：`open` / `wpa2` / `wpa3` / `wpa` / `unknown`（源自 `nmcli` SECURITY 字段映射）。

## 7. 配网状态机

设备侧 `state_update.state` 取值；App 侧镜像为 `ProvisioningState`：
```
IDLE ──scan_wifi──▶ SCANNING ──scan_results──▶ SCANNING(结果)
                                          │
   (set_config) Applying ──apply──▶ APPLYING ──▶ CONNECTING ──▶ CONNECTED(ip,ssid)
                          │                │
                          └──────── error ──────────▶ FAILED(code)
任意非终态 ──cancel──▶ IDLE
```
- `CONNECTED`：wlan0 已切 STA 并拿到路由器分配 IP；携带 `ip` 供 App 验证 `http://<ip>:8000`。
- `FAILED`：切网/应用失败；设备已回退到原热点，BLE 仍连接，可重试。

## 8. 错误码（STATUS `error.code`）

| code | 含义 |
|---|---|
| `bad_frame` | 分帧/CRC 校验失败（App 应重发） |
| `bad_request` | 命令 schema 不合法 |
| `scan_failed` | WiFi 扫描失败（`nmcli` 出错） |
| `wifi_connect_failed` | 连不上目标 AP（密码错/超时/无信号） |
| `apply_failed` | dhlr 内部接口应用配置失败 |
| `busy` | 已有配网进行中 |
| `internal` | 其他内部错误（附 `message`） |

## 9. 版本与扩展

- `DEVICE_INFO.proto` 为版本协商唯一入口；不匹配直接拒绝，保证前向兼容。
- 扩展新命令/状态/错误码属于**同 proto 内增量**，旧端遇未知字段忽略（JSON 解析 `ignoreUnknownKeys`）。
- 鉴权（AUTH 特征值）为预留扩展，启用时仅在首个 `set_config` 前增加一次握手，不改动分帧/状态机/命令形状。

## 10. 安全说明（开发期）

- 当前**无鉴权**：BLE 明文承载 WiFi 密码与 token，任意蓝牙范围内设备可发起配网。**上线前必须启用 AUTH**（见 §2、§9）。
- dhlr 内部 `/internal/apply-provisioning` 接口仅监听 `127.0.0.1`，防 LAN 越权。
