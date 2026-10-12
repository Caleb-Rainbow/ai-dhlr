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
- 含 `network` 时可超过单次 Read 响应。bless 0.3.0 的 BlueZ 原始实现忽略 `options.offset`，服务启动前通过 `src/ble/bless_compat.py` 适配 D-Bus 调度：首次读取固定快照，后续 Read Blob 返回同一快照的 `value[offset:]`，越界返回 `org.bluez.Error.InvalidOffset`。

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
- 配置的 `DEFAULT_MTU=247` 只是上限，不能当作真实连接 MTU。BLE 服务在 BlueZ ReadValue/WriteValue 的 `options.mtu` 中观察实际 MTU，未知时按 MTU 23（20B）通知；多个已连接中心取保守最小值。已协商 MTU 185 的 iPhone 通知最多 182B，MTU 247 的 Android 仍为最多 244B。
- BlueZ 会截断超长通知，不会补发尾部。2026-10-11 iPhone 16 Pro 实测日志的两包 182B 与旧服务固定 244B 分片吻合；模拟传输已复现配置帧不完整。客户端增大超时、修补 JSON 或仅减小 REQUEST 写入分片无法恢复这些丢失字节。
- **写侧（REQUEST）**：多块按序写入 REQUEST 特征值，**每块等 `onWrite` 成功回调后再发下一块**，严格不交错（避免 GATT 133/0x85 竞态）。
- **收侧**：按序累积块字节 → 解析（遇 `magic` 起帧，凑齐 `len`+2 校验 `crc16`）→ 校验通过解 JSON；CRC 错 → 设备回 `error{code:"bad_frame"}`，App 重发整帧。
- 单帧大多 < 一块（device_info、set_config 通常 < 244B）；`scan_results` 可能多块。

## 5. 命令（REQUEST 载荷 JSON）

所有命令对象：`{"cmd": "<name>", "id": <u32>, ...}`。`id` 由 App 生成，设备在对应 `state_update`/`error` 回引，便于匹配。

| cmd | 字段 | 说明 |
|---|---|---|
| `scan_wifi` | — | 触发设备 `nmcli` 扫描周边 WiFi，结果异步经 STATUS `scan_results` 回传 |
| `set_config` | `config: {wifi:{ssid,password,ipv4?}, ethernet:{ipv4}, remote:{...}, system:{...}}` | 下发配置（不立即生效）；WiFi / 以太网一次只修改一个接口 |
| `apply` | — | 应用上次 `set_config`：配置 wlan0 / eth0 + 可选调用 dhlr 内部接口落 remote/system |
| `cancel` | — | 取消进行中的扫描/应用（尽力） |
| `get_config` | — | 读取当前 remote/system 及本功能保存的网络 profile，经 STATUS 回传，用于重新配网回填 |

`set_config.config` 示例：
```json
{
  "wifi":   { "ssid": "TP-LINK_XC", "password": "********" },
  "remote": { "enabled": true, "server_url": "https://vis.example.com", "token": "eyJ..." },
  "system": { "name": "1号厨房监测", "device_id": "C206B6AF476AEE73" }
}
```
> 字段子集允许：只下发变化的键；未下发键保持不变。App 端 `DeviceProfile.buildConfig` 决定哪些键。

### 5.1 WiFi / 以太网静态 IPv4

新版设备的 `current_config` 事件带有顶层 `network_config_version: 1`。小程序（包括 iOS）
和 App 从分帧配置应答判断能力；旧设备没有该字段时，只允许原有 WiFi DHCP 配网，
静态 IP / 以太网提交会提示升级，防止旧固件忽略新字段而误报成功。`proto` 保持兼容。

WiFi 静态配置示例：

```json
{"cmd":"set_config","id":10,"config":{"wifi":{"ssid":"Office","password":"********","ipv4":{"mode":"static","address":"192.168.2.100","netmask":"255.255.255.0","gateway":"192.168.2.1","dns":["192.168.2.1"]}}}}
```

以太网不需要 SSID / WiFi 密码：

```json
{"cmd":"set_config","id":11,"config":{"ethernet":{"ipv4":{"mode":"static","address":"192.168.1.100","netmask":"255.255.255.0","gateway":"192.168.1.1","dns":["192.168.1.1"]}}}}
{"cmd":"apply","id":12}
```

- `mode`：`dhcp` / `static`。旧 WiFi 载荷不含 `ipv4` 时默认 DHCP。
- 静态模式必填 `address` 和点分十进制 `netmask`，掩码须连续且非全零。
  不允许网络/广播地址（/31、/32 点对点地址除外）、回环或非单播 IPv4。
- `gateway`、`dns` 可省略或留空；纯局域网通信可不设置。网关须为同一子网内的其他主机，
  DNS 为至多 4 个 IPv4 地址。访问域名或其他网段时按实际网络填写。
- 客户端、设备端均先校验。拒绝无效 `set_config` 后清空旧待应用载荷，后续 `apply` 不会应用旧配置。
- 切回 DHCP 下发 `ipv4: {"mode":"dhcp"}`，设备清空静态地址、网关、DNS 和忽略自动 DNS 的设置。
- 持久化使用 NetworkManager：`wlan0` 对应 `provisioned-wifi`，`eth0` 对应
  `provisioned-ethernet`。静态模式使用 `ipv4.method=manual`，自动模式使用 `auto`，
  并要求 IPv4 就绪才报告连接成功。参数说明见 [NetworkManager 官方文档](https://www.networkmanager.dev/docs/api/latest/settings-ipv4.html)。
- 以太网用候选连接 UUID 激活；失败删除候选并恢复原活动连接。成功只清理本功能创建的旧以太网 profile，
  不删除系统预置连接，不切换 WiFi/热点。WiFi 失败保留原有热点回退机制。
- 客户端可以关闭“同时更新设备配置”，仅下发网络子块，不修改服务器、设备 ID 和名称。
  已保存的静态参数通过 `get_config` 回填，不回传 WiFi 密码。
- `CONNECTED` 携带本次接口的 `ip`；只有 WiFi 带 `ssid`。以太网成功不复用旧 WiFi 的 SSID。
  连接成功表示地址已生效，不代表服务器登录或业务通信成功。
- 如果开启“开机自启热点”，启动脚本仍会按该设置切换到热点；要开机使用保存的 WiFi 配置，关闭该选项。

2026-10-11 真机验证：`192.168.2.158`，通过 SSH 调用设备真实配网状态机及 nmcli，
命令经过协议分帧编码/解码后分派。WiFi `192.168.2.158/24`（网关/DNS `192.168.2.1`）
和以太网 `192.168.0.235/23`（网关/DNS `192.168.1.1`）均通过静态应用、profile 保存、
配置回读和切回 DHCP 检查；测试后恢复原连接及 DHCP。测试源文件备份位于设备
`/home/linaro/dhlr-static-network-test-20261011-130234/source-backup`。
上述首轮 SSH 验证未经过手机蓝牙空口，也未在关闭 DHCP 的路由器、设备重启或手机 UI 上验收。

同日补充 Android 真机验收：手机 `192.168.2.111:5555`（REDMI K80 Pro，Android 16 / API 36）
安装本次 Debug APK，通过 App 界面连接 `AILRBJ26082008`（蓝牙硬件 ID `74-24-ca-aa-fb-24`）。
两项显式启用的设备测试覆盖以太网与 WiFi：非法掩码拦截、静态 IP 下发成功、退出再进入后的
静态值回填、切回 DHCP，以及设备列表无重复记录。蓝牙服务日志确认收到 `get_config`、
`set_config`、`apply`，并返回配置/状态通知；SSH 独立读取确认静态 `manual`、地址/掩码、
网关/DNS及后续 `auto` 清空静态值。测试后恢复原连接与 DHCP；此轮未测试设备重启和 iPhone。

验收中修复两处客户端问题：网络单独更新时合并原有本地配置缓存，保留服务器/编号/上报状态；
Android 首次配置回读完成前保持连接阶段，防止过早选接口把未到达的静态配置显示为 DHCP。
测试源文件位于 Android 仓库 `app/src/androidTest/.../provisioning/StaticIpDeviceAcceptanceTest.kt`，
普通运行默认跳过，只有 `enableLiveNetworkTest=true` 和完整设备参数才会改变外设网络。

同日补充微信小程序真机验收：同一 Android 手机运行 uni-app 主包挂载的 BluetoothGateway
分包，通过小程序界面连接同一设备。WiFi 和以太网均完成静态配置、退出重新进入后的四项
参数回填及切回 DHCP；SSH 独立读取确认 `manual` 参数与界面一致，恢复 `auto` 后静态
地址、网关和 DNS 均为空。非法 IP 在客户端被拦截，设备列表和远程上报数量始终为 11。
测试后恢复测试前的 WiFi/以太网连接配置并删除测试 profile，两个服务保持运行。
此轮使用现有开启 DHCP 的局域网验证静态配置生效，未覆盖关闭 DHCP 的路由器、设备
重启、iPhone 和独立原生小程序入口。

小程序验收中发现配置页面停留时蓝牙可能断开。现在会显示断连状态；用户再次提交时
重新连接、订阅和读取设备配置，保留已输入的网络及设备字段，再发送本次提交。
若在应用过程中断连，提示结果尚未确认，不自动重放请求。最新版本已通过实际断连后
重连提交的验证，并补充保留用户输入的模拟回归。

同日代码审查修复验收：`192.168.0.235` 上通过临时虚拟网口制造真实 DHCP 超时，
约 30.9 秒后返回 `ethernet_connect_failed` 并恢复 `IDLE`；原连接恢复、候选 profile
删除、随后扫描/取消及静态配置重试均通过。物理 `eth0` 的 `192.168.0.235/23`
静态地址、网关/DNS、配置回读及切回 DHCP 均通过；测试后恢复原有线连接和 DHCP，
WiFi 保持原连接，临时网口/profile 已清理。设备和开发机各 119 项相关测试通过。
本轮通过 SSH 调用协议分帧及真实配网状态机/nmcli，未经过手机蓝牙空口。

旧设备升级脚本也在该设备的独立克隆仓库验证：root 属主的 `.git/FETCH_HEAD`
会使旧顺序首次 fetch 失败；修复后先归还属主，再执行 fetch/reset，阶段 1—3 通过。
未在运行仓库执行完整升级或 reset。修复源码已部署，BLE 重启后广播及主应用桥连接正常。
源码备份、测试脚本和结果位于 `/home/linaro/dhlr-review-fixes-20261011-145435`。

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
- `CONNECTED`：本次 WiFi / 以太网接口的 DHCP 或静态 IPv4 已就绪；携带 `ip` 供 App 验证 `http://<ip>:8000`。
- `FAILED`：切网/应用失败；WiFi 走原有热点回退，以太网恢复原活动连接。蓝牙仍连接时可重试。

## 8. 错误码（STATUS `error.code`）

| code | 含义 |
|---|---|
| `bad_frame` | 分帧/CRC 校验失败（App 应重发） |
| `bad_request` | 命令 schema 不合法 |
| `scan_failed` | WiFi 扫描失败（`nmcli` 出错） |
| `wifi_connect_failed` | 连不上目标 AP（密码错/超时/无信号） |
| `ethernet_connect_failed` | 有线连接激活失败或 IPv4 超时；尝试恢复原连接后回到 `IDLE` |
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

## 11. iOS 与 BlueZ 反向服务发现

本服务的角色是 BLE 外设。BlueZ 默认会在手机连接后反向发现手机的 GATT 服务；
在 BlueZ 5.66 的入站连接中，`ReverseServiceDiscovery=false` 会跳过自动 GATT 客户端初始化，
外设 GATT 服务继续工作。参见 [官方配置](https://github.com/bluez/bluez/blob/5.66/src/main.conf)
及 [device.c 的 gatt_client_init](https://github.com/bluez/bluez/blob/5.66/src/device.c)。

2026-10-12 在 `192.168.0.236` 的 BlueZ 5.66 / Linux 6.1.118 设备上，经用户授权，
备份 `/etc/bluetooth/main.conf` 后，在现有 `[General]` 段中添加：

```ini
ReverseServiceDiscovery = false
```

随后重启 `bluetooth.service` 与 `ai-dhlr-ble.service`，重新注册 GATT 和广播。
AI 主应用保持原进程。现场备份是 `/etc/bluetooth/main.conf.codex-backup-20261012-ios`，
恢复时使用备份并再次重启这两个服务。需要读取入站对端 GATT 服务的设备应单独评估该配置。

验收设备为 iPad8,9 / iOS 26.5 / 微信 8.0.79 / SDK 3.17.4，主项目蓝牙网关分包
`ble-20261012-5`。用户反复测试确认持续读取、子页切换、返回列表后重新进入详情均正常。
客户端应答序号 192–206 与设备日志匹配，包含三轮参数、音量增益、状态、系统信息和日志列表。
这次现场有效措施是配置修改加服务重新注册；尚未独立分离两者影响，也未验证其他 iOS 型号。
已保存抓包中没有设备向手机发起的反向 Read Request，空口 Write Response 与手机回调仍应分别检查。

### 安装与旧设备升级自动迁移

`deploy/bootstrap-ble.sh` 调用标准库脚本 `deploy/configure_bluez.py`，在现有
`/etc/bluetooth/main.conf` 的 `[General]` 中设置 `ReverseServiceDiscovery=false`。
其他选项、注释、换行格式和文件权限保留；修改前保存同目录的
`main.conf.dhlr-backup-<随机标识>`，原文件不存在时创建配置。重复部署不再修改已正确的
配置，也不会因此重复重启 Bluetooth。配置格式错误会报错并保留原文件。

配置变化时先重启 `bluetooth.service`，再由部署脚本启动或重启 `ai-dhlr-ble.service`，
重新注册 GATT 并加载新代码。Bluetooth 重启失败时恢复原配置，尝试恢复原服务并报错，
后续可以重新执行部署。若 BLE 启动失败，查看部署输出及对应的 systemd 日志。

旧版“系统更新”进程在拉取代码后执行磁盘上的新版 `bootstrap-ble.sh`，因此即使更新器
尚未升级，本次更新也能迁移配置。命令行 `update.sh` 同样在重启主服务前调用此入口。
单独补齐配置可执行 `sudo bash deploy/bootstrap-ble.sh`；该操作会重启 BLE 服务。

### 分帧设备信息（仅蓝牙模式也可读取）

`current_config` 增加可选顶层 `device_info`，字段与 `DEVICE_INFO` 的裸 JSON 相同：
`type/model/fw/hwid/name/proto/network`。原配置字段和 `network_config_version` 保持不变，
旧客户端可忽略新增字段。`GattServer.device_info()` 复用后台网络缓存，
通过 `ProvisioningService.device_info_provider` 注入，不在 BLE 写回调中查询网络或修改连接。
这样 iOS 可通过已协商 MTU 的分帧通知读取信息，避免长特征读取。

2026-10-12 在用户提供的当前地址 `192.168.0.235` 上，备份后仅补充三个 BLE 文件的上述接口，
保留设备其他代码和 `ReverseServiceDiscovery=false`，只重启 `ai-dhlr-ble`。
AI 主应用 PID=2606 保持不变。原文件备份为
`/home/linaro/codex-backups/ble-info-20261012-6e51d3986d`。
直接运行只读 `get_config` 应答路径，确认 fw=`1.0.9`、model=`ai-dhlr`、IP=`192.168.0.235`；
MTU=185 时六个分片均不超过 182B，协议往返解码一致。此核对未经过手机蓝牙空口，
需主项目小程序 `ble-20261012-7` 进行显示验收。
