#!/bin/bash
# 旧设备（1 月代码）一键手动升级脚本 —— 在开发机上运行，通过 SSH 驱动设备
#
# 背景：停在 2026-01 代码（b37c3c2 附近）的旧设备点"系统更新"毫无反应：
#   旧 _trigger_update 用 DEVNULL 吞掉全部输出，git pull 在合并前被
#   ① update.sh 本地 chmod 模式改动 ② 未跟踪的定制 audio_assets/*.wav
#   挡住而静默中止。本脚本从外部一次性完成整个修复+升级流程。
#
# 用法：./deploy/upgrade-old-device.sh <设备IP> [用户名] [密码]
#   用户名/密码默认 linaro/linaro（车队默认）。可重复执行（幂等）。
#
# 流程：
#   [0] TUN 假开放控制组测试（v2rayN TUN 会假接受所有 TCP，必须先排除）
#   [1] 预检：SSH 连通、磁盘空间
#   [2] 清除升级阻碍（root 属主文件归还 linaro；本地修改/未跟踪冲突由 reset --hard 覆盖，定制音频不备份）
#   [3] git fetch + reset --hard 强制对齐 origin/main（与新版前端按钮同语义）
#   [4] 检测并安装新增 Python 依赖（aiohttp/psutil/bless，按需 pip）
#   [5] sudo bash update.sh（service 同步、日志整治、时间同步、OTG 守护、重启）
#   [6] sudo bash deploy/bootstrap-ble.sh（BLE 配网服务，幂等）
#   [7] 验证：服务状态、版本、启动日志 ERROR 摘要

set -euo pipefail

IP="${1:?用法: $0 <设备IP> [用户名] [密码]}"
USER_NAME="${2:-linaro}"
PASS="${3:-linaro}"

command -v sshpass >/dev/null || { echo "[FAIL] 本机缺少 sshpass"; exit 1; }

# ---------- [0] TUN 控制组测试 ----------
# 探测同网段一个不存在的地址：若 22 端口"开放"说明 TUN 在假接受，后续全不可信
CTRL="${IP%.*}.99"
[ "$CTRL" = "$IP" ] && CTRL="${IP%.*}.98"
if timeout 5 bash -c "cat < /dev/null > /dev/tcp/$CTRL/22" 2>/dev/null; then
    echo "[FAIL] 控制组地址 $CTRL:22 假开放——本机 TUN（v2rayN）正在劫持连接，请关闭后重试"
    exit 1
fi
echo "[0] TUN 控制组测试通过（$CTRL 不可达，网络可信）"

SSH_CMD=(sshpass -p "$PASS" ssh -o StrictHostKeyChecking=no -o ConnectTimeout=10 "$USER_NAME@$IP")

# ---------- SSH 连通预检 ----------
if ! "${SSH_CMD[@]}" "echo ok" >/dev/null 2>&1; then
    echo "[FAIL] SSH 连接 $USER_NAME@$IP 失败（检查设备在线/账号密码）"
    exit 1
fi
echo "[0] SSH 连接正常：$USER_NAME@$IP"

# ---------- [1]-[7] 设备侧流程 ----------
# SUDO_PASS 经命令行环境变量传入设备侧（车队默认 linaro/linaro，风险可接受）
"${SSH_CMD[@]}" "SUDO_PASS='$PASS' bash -s" <<'REMOTE'
set -euo pipefail
die() { echo "[FAIL] $*" >&2; exit 1; }
step() { echo; echo "[$1] $2"; }

APP_DIR=/home/linaro/ai-dhlr
SUDO_PASS="${SUDO_PASS:-linaro}"
cd "$APP_DIR" 2>/dev/null || die "项目目录不存在: $APP_DIR"

# 设备 sudo：车队镜像为 NOPASSWD；否则退回密码管道
sudo_run() {
    if sudo -n true 2>/dev/null; then sudo "$@";
    else printf '%s\n' "$SUDO_PASS" | sudo -S -p '' "$@"; fi
}

step 1/7 "预检"
DISK=$(df -P / | awk 'NR==2{gsub(/%/,"");print $5}')
[ "$DISK" -gt 90 ] && echo "  [WARN] 根分区已用 ${DISK}%，update.sh 的日志整治会清理，继续"
echo "  根分区已用 ${DISK}%"
step 2/7 "清除升级阻碍（定制音频按约定不备份，reset --hard 直接覆盖）"
# 1 月 sudo 部署常留下 root 属主的 audio_assets/（.220/.221/.222 实测），
# linaro 无法写入会被 fetch/reset 卡住；首次 Git 调用前归还属主，修复 .git 污染。
sudo_run chown -R "$(id -u):$(id -g)" "$APP_DIR" || die "chown 归还属主失败"
echo "  已确保 $APP_DIR 属主为 $(whoami)"
# 本地修改的跟踪文件（如 update.sh 模式位）与未跟踪的冲突文件（定制 wav）
# 均无需备份，交由下一步 reset --hard 强制覆盖

step 3/7 "拉取远端并强制对齐当前分支（与新版更新按钮同语义）"
echo "  当前提交: $(git log --oneline -1 2>/dev/null || echo '非 git 仓库')"
git fetch origin || die "git fetch 失败（外网/SSH 密钥/known_hosts），手动在设备上排查"
BRANCH=$(git rev-parse --abbrev-ref HEAD)
BEHIND=$(git rev-list --count "HEAD..origin/$BRANCH" 2>/dev/null || echo "?")
echo "  分支 $BRANCH 落后 origin $BEHIND 个提交"
git reset --hard "origin/$BRANCH" >/dev/null || die "git reset 失败"
echo "  已更新到: $(git log --oneline -1)"

step 4/7 "补装新增 Python 依赖"
# 从 service 文件解析解释器路径，失败则退回 dhlr_env 默认路径
PY_BIN=$(sed -n 's/^ExecStart=\([^ ]*\).*/\1/p' /etc/systemd/system/ai-dhlr.service 2>/dev/null | head -1)
[ -x "$PY_BIN" ] || PY_BIN=/home/linaro/miniforge3/envs/dhlr_env/bin/python
[ -x "$PY_BIN" ] || PY_BIN=$(command -v python3)
echo "  解释器: $PY_BIN"
for mod in aiohttp psutil bless; do
    if "$PY_BIN" -c "import $mod" >/dev/null 2>&1; then
        echo "  已安装: $mod"
    else
        "$PY_BIN" -m pip install -q "$mod" && echo "  已安装: $mod（新装）" \
            || echo "  [WARN] $mod 安装失败（aiohttp/psutil 缺失会导致主服务起不来，需人工处理）"
    fi
done
"$PY_BIN" -c "import rknnlite" >/dev/null 2>&1 \
    || echo "  [WARN] rknnlite 缺失：config engine=rknn 时无法推理，需手动装本地轮子"

step 5/7 "执行 update.sh（service 同步/日志整治/时间同步/OTG 守护/重启主服务）"
sudo_run bash update.sh || die "update.sh 执行失败"

step 6/7 "部署 BLE 配网服务（幂等，失败不阻断）"
if sudo_run bash deploy/bootstrap-ble.sh; then echo "  BLE 部署完成"; else
    echo "  [WARN] BLE 部署失败，主服务不受影响，可稍后单独重试"; fi

step 7/7 "验证"
sleep 15
if systemctl is-active --quiet ai-dhlr.service; then
    echo "  主服务: active (running)"
else
    echo "  [FAIL] 主服务未运行，最近日志："
    journalctl -u ai-dhlr.service -n 30 --no-pager | sed 's/^/    /'
    exit 1
fi
echo "  版本: $(git log --oneline -1)"
ERRN=$(journalctl -u ai-dhlr.service --since '-2 min' --no-pager 2>/dev/null | grep -c '\[ERROR\]' || true)
if [ "$ERRN" -gt 0 ]; then
    echo "  [WARN] 启动后 2 分钟内有 $ERRN 条 [ERROR]（摄像头/串口类需人工确认）："
    journalctl -u ai-dhlr.service --since '-2 min' --no-pager | grep '\[ERROR\]' | sort | uniq -c | sort -rn | head -5 | sed 's/^/    /'
fi
systemctl is-active --quiet ai-dhlr-ble.service 2>/dev/null && echo "  BLE 服务: active" || echo "  BLE 服务: 未运行，请查看部署输出及 journalctl -u ai-dhlr-ble.service"
echo
echo "==== 升级完成 ===="
REMOTE
