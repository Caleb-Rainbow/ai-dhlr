#!/bin/bash
# 热点脚本幂等部署（随"系统更新"流程调用，以 root 运行；脚本内部命令不再各自 sudo）。
#
# 做两件事：
# 1. 把仓库内 deploy/auto_hotspot.sh 安装/更新到 /usr/local/bin/auto_hotspot.sh
#    （开机由 hotspot-startup.service 调用）。
# 2. 一次性清理历史残留的 Hotspot / Hotspot-N 连接——新脚本改用固定连接名
#    dhlr-hotspot，不再产生 Hotspot-N；旧连接清掉避免堆积。
#
# 幂等：可反复执行；cmp 一致则跳过拷贝；无残留则跳过清理。退出码 0。
# 调用方：src/api/ws_handler.py::_deploy_and_restart（紧跟 bootstrap-ble.sh 之后）。

set -u

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
SRC_SCRIPT="$PROJECT_ROOT/deploy/auto_hotspot.sh"
DST_SCRIPT="/usr/local/bin/auto_hotspot.sh"

echo "==== 热点脚本部署开始 $(date '+%F %T') ===="

# ---------- 1. 安装/更新运行脚本 ----------
if [ ! -f "$SRC_SCRIPT" ]; then
    echo "[FAIL] 仓库内热点脚本不存在: $SRC_SCRIPT"
    exit 1
fi

NEED_INSTALL=0
if [ ! -f "$DST_SCRIPT" ]; then
    NEED_INSTALL=1
elif ! cmp -s "$SRC_SCRIPT" "$DST_SCRIPT"; then
    NEED_INSTALL=1
fi
if [ "$NEED_INSTALL" -eq 1 ]; then
    cp "$SRC_SCRIPT" "$DST_SCRIPT"
    chmod 755 "$DST_SCRIPT"
    echo "[OK]   已安装/更新 $DST_SCRIPT"
else
    echo "[SKIP] $DST_SCRIPT 已是最新"
fi

# ---------- 2. 清理历史 Hotspot / Hotspot-N 残留连接 ----------
# 一次 nmcli 调用取所有连接及其 DEVICE（远快于逐条查询）；
# 仅删 "Hotspot" / "Hotspot-<数字>" 且非活动的连接——新脚本固定用
# dhlr-hotspot，绝不误删；活动中的连接跳过避免误伤提供网络的连接。
CLEANED=0
while IFS=: read -r name dev; do
    case "$name" in
        Hotspot|Hotspot-[0-9]*) ;;
        *) continue ;;
    esac
    if [ -n "$dev" ] && [ "$dev" != "--" ]; then
        echo "[SKIP] $name 正活动于 $dev，跳过删除"
        continue
    fi
    nmcli connection delete "$name" >/dev/null 2>&1 && CLEANED=$((CLEANED + 1))
done < <(nmcli -t -f NAME,DEVICE connection show 2>/dev/null)

if [ "$CLEANED" -gt 0 ]; then
    echo "[OK]   清理 $CLEANED 个历史 Hotspot 残留连接"
else
    echo "[SKIP] 无历史 Hotspot 残留连接"
fi

echo "==== 热点脚本部署完成 ===="
exit 0
