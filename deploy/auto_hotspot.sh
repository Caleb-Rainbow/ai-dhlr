#!/bin/bash
# 开机自启热点脚本（开机由 hotspot-startup.service 调用；以 root 运行）。
#
# 设计要点（修复历史 Hotspot-N 滚雪球问题）：
# 1. 固定连接名 dhlr-hotspot → 每次「先删后建」同名连接，永远只有一个，
#    不再产生 Hotspot / Hotspot-1 / Hotspot-2 ... 的自增堆积。
# 2. SSID 由 wlan0 MAC 末 4 位派生（如 dhlr-7857）→ 开机稳定不变，
#    便于用户记忆与扫码；MAC 取不到时兜底为 dhlr。
# 3. ipv4.method shared 由 nmcli hotspot 默认开启（自动 DHCP/NAT）。
#
# 部署：由 deploy/bootstrap-hotspot.sh 幂等安装到 /usr/local/bin/auto_hotspot.sh。

set -u

INTERFACE="${INTERFACE:-wlan0}"
CONN_NAME="dhlr-hotspot"
PASSWORD="12345678"

# ---- 由 wlan0 MAC 末 4 位生成稳定 SSID ----
MAC=$(cat "/sys/class/net/${INTERFACE}/address" 2>/dev/null)
SUFFIX="${MAC//:/}"          # 去冒号：dc:ec:4f:7e:78:57 -> dcec4f7e7857
SUFFIX="${SUFFIX: -4}"       # 末 4 位 -> 7857
if [ -n "$SUFFIX" ]; then
    SSID="dhlr-${SUFFIX^^}"  # 大写 -> dhlr-7857
else
    SSID="dhlr"              # 网卡未就绪兜底
fi

# ---- 幂等：同名连接先删后建，杜绝累积 ----
nmcli connection delete "$CONN_NAME" >/dev/null 2>&1

nmcli device wifi hotspot \
    ifname "$INTERFACE" \
    con-name "$CONN_NAME" \
    ssid "$SSID" \
    password "$PASSWORD"

echo "热点已启动: $SSID  密码: $PASSWORD  (连接名: $CONN_NAME)"

# ---- RKNN / 调试节点权限（保留原有行为）----
chmod 755 /sys/kernel/debug
chmod 666 /sys/kernel/debug/rknpu/load 2>/dev/null || true
