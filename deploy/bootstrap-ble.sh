#!/bin/bash
# ai-dhlr BLE 配网服务幂等部署脚本
#
# 用途：旧设备零干预升级时，由"系统更新"按钮（src/api/ws_handler.py::_trigger_update）
#       在 git pull 之后以 root 调用本脚本，完成 BLE 运行所需的系统层配置。
#
# 以 root 运行（调用方：sudo bash deploy/bootstrap-ble.sh），脚本内部命令不再各自 sudo。
# 幂等：bluez 已装 / unit 已注册且最新 / 已 enable 均跳过；可反复执行，也可单独 SSH 调试。
# 退出码：0 = 成功，或可容忍降级（如 bluez 因网络装失败但继续，仅告警）；
#         1 = 硬错误（仓库内 unit 文件缺失等，无法继续）。

set -u
export DEBIAN_FRONTEND=noninteractive

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
SRC_UNIT="$PROJECT_ROOT/deploy/ai-dhlr-ble.service"
DST_UNIT="/etc/systemd/system/ai-dhlr-ble.service"
BLE_UNIT_NAME="ai-dhlr-ble.service"

echo "==== BLE 部署开始 $(date '+%F %T') ===="

# ---------- 1. bluez 系统依赖检测兜底 ----------
# 旧设备系统镜像大概率已自带 bluez（Debian12 标配）；仅在缺失时才尝试 apt，避免无谓联网。
if systemctl is-active bluetooth --quiet 2>/dev/null && dpkg -s bluez >/dev/null 2>&1; then
    echo "[SKIP] bluez 已安装且 bluetooth.service 运行中"
else
    echo "[INFO ] bluez 缺失，尝试 apt 安装（超时 120s，失败不阻断）..."
    if timeout 120 apt-get update -y && timeout 120 apt-get install -y --no-install-recommends bluez bluez-tools; then
        systemctl enable --now bluetooth.service >/dev/null 2>&1 || true
        echo "[OK]   bluez 安装完成"
    else
        # 仅告警不退出：主服务（火灾监测）重启不受影响；BLE 配网在该设备暂不可用。
        echo "[WARN] bluez apt 安装失败（网络/源问题），跳过；镜像已自带则不受影响"
    fi
fi

# ---------- 2. 校验仓库内 unit 文件存在（硬前置） ----------
if [ ! -f "$SRC_UNIT" ]; then
    echo "[FAIL] 仓库内 unit 文件不存在: $SRC_UNIT"
    exit 1
fi

# ---------- 3. 注册 / 更新 systemd unit ----------
NEED_RELOAD=0
if [ ! -f "$DST_UNIT" ]; then
    echo "[INFO ] 首次注册 unit -> $DST_UNIT"
    cp "$SRC_UNIT" "$DST_UNIT"
    NEED_RELOAD=1
elif ! cmp -s "$SRC_UNIT" "$DST_UNIT"; then
    echo "[INFO ] unit 文件有更新，覆盖 -> $DST_UNIT"
    cp "$SRC_UNIT" "$DST_UNIT"
    NEED_RELOAD=1
else
    echo "[SKIP] unit 文件已是最新"
fi
if [ "$NEED_RELOAD" -eq 1 ]; then
    systemctl daemon-reload
    echo "[OK]   systemctl daemon-reload 完成"
fi

# ---------- 4. enable ----------
if systemctl is-enabled "$BLE_UNIT_NAME" >/dev/null 2>&1; then
    echo "[SKIP] $BLE_UNIT_NAME 已 enable"
else
    if systemctl enable "$BLE_UNIT_NAME" >/dev/null 2>&1; then
        echo "[OK]   $BLE_UNIT_NAME 已 enable"
    else
        echo "[WARN] $BLE_UNIT_NAME enable 失败"
    fi
fi

echo "==== BLE 部署完成 ===="
exit 0
