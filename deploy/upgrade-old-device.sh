#!/bin/bash
# 旧设备手动升级入口：在开发机通过 SSH 驱动设备，适用于旧更新按钮无法工作的设备。
#
# 使用方法（开发机需要 Bash、sshpass、ssh；Windows 请使用具备这些工具的 Linux/WSL）：
#   bash deploy/upgrade-old-device.sh <设备IP或主机名> [用户名] [密码]
#   bash deploy/upgrade-old-device.sh 192.168.0.244
# 用户名/密码默认 linaro/linaro；第三个参数同时用于 SSH 和 sudo。
#
# 升级前提：
#   1. 设备使用 systemd，项目位于 /home/linaro/ai-dhlr，运行账号为 linaro。
#      SSH 账号须有 sudo 权限；设备须能访问 Git 远端及 Python/系统软件源。
#      新 unit 指定的 Python 环境须已存在；RKNN 本地轮子须匹配设备架构与 Python ABI。
#   2. 设备当前分支必须有同名 origin 分支，不支持 detached HEAD。
#      本脚本按设备当前分支升级，并非固定 main。先确认目标分支，避免升级错分支。
#   3. 待发布代码必须已提交并推送到设备 origin 的目标分支，且包含本次部署配套文件。
#      开发机未提交的改动不会随 SSH 自动上传；不能只复制本脚本就认为整套修复已发布。
#   4. 升级前记录旧提交、摄像头/串口状态，并另行备份 config/config.yaml、service 和依赖清单。
#      脚本会 reset --hard：覆盖已跟踪文件及阻碍检出的定制音频；不备份音频、不清空业务目录。
#      config/config.yaml 与日志为忽略文件；已跟踪的模型仍会随代码更新。
#      应用启动还可能迁移配置，所以备份不能省略。
#   5. 保持 SSH 连接；不要从 ai-dhlr.service 的进程内调用本脚本。
#      重启主服务会终止该 unit 内的更新进程。SSH 中断后先检查设备状态，不要盲目重复运行。
#
# 执行顺序：
#   [1] 检查空间和运行账号，修复旧 sudo 部署造成的仓库属主污染。
#   [2] fetch 后锁定当前分支的远端提交，强制更新工作区。
#   [3] 按新 unit 的 Python 安装 requirements.txt，检查依赖冲突、运行库和模型文件。
#   [4] update.sh --skip-pull 统一同步 service/日志/时间/OTG/热点/BLE，随后重启主服务。
#   [5] upgrade_check.py 检查持续运行状态和应用实际返回的版本。
#   [6] 分别输出 VERSION 与 Git 提交；配套步骤失败不会被主服务启动成功掩盖。
#
# 自动启动验收标准（只有全部满足且部署无失败，设备流程才返回 0）：
#   - 依赖安装、pip check、运行库导入、模型文件检查以及各部署步骤成功。
#   - 在 90 秒观察窗口内，两服务同时 active/running、MainPID > 0；
#     连续至少 15 秒 PID 不变、NRestarts 不增加。检查间隔约 2 秒。
#   - 同一稳定窗口内，本机 /api/device/identify 始终返回 DHLR_DEVICE，
#     firmware 与仓库 VERSION 一致；端口/监听地址读取设备配置，不硬编码版本号。
#   注意：NRestarts 必须不再增加，不要求历史累计值为 0；HTTP 200 本身也不代表通过。
#
# 现场业务验收（脚本不自动触发报警、改 WiFi 或操作继电器）：
#   - 核对原摄像头、监测区域、ROI、串口物理地址；WebSocket 只读查询应成功。
#   - 有 0 的旧串口索引按整组 +1 迁移，如 0/1/2 -> 1/2/3，物理地址不变。
#     无 0 的旧配置或已被错误迁移的配置，必须对照升级前备份核对，不能盲目再 +1。
#   - 需要使用的摄像头应在线且有实时画面，推理应持续产生有效采样；有服务进程不代表有图像。
#   - BLE 日志应显示广播成功，并用真实客户端完成扫描、连接、设备信息读取。
#   - 在现场测试窗口验证电流采集、人员识别、报警/语音和对应区域切电、复位；
#     如要求开机持久化，另行验证重启后的热点、OTG、时间同步等行为。
#   自动启动验收与上述现场验收应分别记录；摄像头或传感器不可用时不能写“全功能验收通过”。
#
# 设备流程退出码：0 = 部署及启动检查通过；1 = 主要步骤/启动检查失败；
#                 2 = 配套部署部分失败，但仍尝试恢复主服务且已通过启动检查。
# SSH/本机命令也可能返回其他非零码；所有非零结果均须结合日志处理，不能视为成功。
# 失败不会自动回滚已更新的代码/依赖/系统配置。日志保存与复查命令见 README“旧设备升级与验收”。
set -euo pipefail

IP="${1:?用法: $0 <设备IP或主机名> [用户名] [密码]}"
USER_NAME="${2:-linaro}"
PASS="${3:-linaro}"
for cmd in sshpass ssh; do
    command -v "$cmd" >/dev/null || { echo "[FAIL] 本机缺少 $cmd"; exit 1; }
done
# sudo 的密码输入以换行分隔；其余字符均作为数据传送，不拼入远端命令。
[[ "$PASS" != *$'\n'* && "$PASS" != *$'\r'* ]] || { echo "[FAIL] 密码不能包含换行"; exit 1; }
export SSHPASS="$PASS"
SSH_CMD=(sshpass -e ssh -o StrictHostKeyChecking=no -o ConnectTimeout=10
    -o ConnectionAttempts=1 -o ServerAliveInterval=15 -o ServerAliveCountMax=3 "$USER_NAME@$IP")

# 必须完成目标设备的 SSH 握手。邻近 IP 可能是真实设备，不能作为 TUN 判据。
if ! "${SSH_CMD[@]}" 'printf dhlr-ssh-ok' </dev/null | grep -qx 'dhlr-ssh-ok'; then
    echo "[FAIL] SSH 连接 $USER_NAME@$IP 失败（检查设备在线、账号密码及代理/TUN）"
    exit 1
fi
echo "[0] SSH 连接正常：$USER_NAME@$IP"

# 密码单独占标准输入首行。远端先读取，再将剩余内容交给 bash，避免 shell 转义和注入。
{
printf '%s\n' "$PASS"
cat <<'REMOTE'
set -euo pipefail
die() { echo "[FAIL] $*" >&2; exit 1; }
step() { echo; echo "[$1] $2"; }
APP_DIR=/home/linaro/ai-dhlr
APP_USER=linaro
cd "$APP_DIR" 2>/dev/null || die "项目目录不存在: $APP_DIR"

sudo_run() {
    # NOPASSWD 镜像无需密码；否则仅把密码送给 sudo，避免命令读走后续远端脚本。
    if sudo -n true </dev/null 2>/dev/null; then
        sudo -n -- "$@" </dev/null
    else
        printf '%s\n' "$SUDO_PASS" | sudo -S -p '' -- "$@"
    fi
}
# 无论 SSH 用哪个账号，Git 和 Python 都以实际运行主服务的 linaro 执行。
app_run() {
    if [ "$(id -un)" = "$APP_USER" ]; then
        "$@" </dev/null
    else
        sudo_run runuser -u "$APP_USER" -- env HOME=/home/linaro "$@"
    fi
}

step 1/6 "预检及修复仓库属主"
id "$APP_USER" >/dev/null || die "运行账号不存在: $APP_USER"
DISK=$(df -P "$APP_DIR" | awk 'NR==2{gsub(/%/,"");print $5}')
echo "  项目所在分区已用 ${DISK}%"
if [ "$DISK" -gt 90 ]; then
    echo "  [WARN] 空间紧张；若 git/pip 报空间不足，请先清理日志再重试"
fi
# 必须在首次 Git 写操作前处理 .git/objects 和音频目录可能残留的 root 属主。
sudo_run chown -R "$APP_USER:$(id -gn "$APP_USER")" "$APP_DIR" || die "chown 归还属主失败"

step 2/6 "拉取并强制对齐当前分支（定制音频不备份）"
BRANCH=$(app_run git symbolic-ref --quiet --short HEAD) || die "当前为 detached HEAD，请先切换到需要升级的分支"
app_run git fetch origin || die "git fetch 失败，请检查外网、部署密钥和 known_hosts"
TARGET=$(app_run git rev-parse --verify "refs/remotes/origin/$BRANCH^{commit}") || die "远端分支不存在: origin/$BRANCH"
# 只 reset 到刚刚解析出的提交；不使用 git clean，避免清理未跟踪的配置和业务产物。
app_run git reset --hard "$TARGET" || die "git reset 失败"

step 3/6 "按新版 service 的解释器安装依赖"
# 不读取将被覆盖的旧 unit，也不回退到系统 Python，否则可能安装成功但服务环境仍缺包。
PY_BIN=$(sed -n 's/^ExecStart=\([^ ]*\).*/\1/p' deploy/ai-dhlr.service)
BLE_PY_BIN=$(sed -n 's/^ExecStart=\([^ ]*\).*/\1/p' deploy/ai-dhlr-ble.service)
[ -n "$PY_BIN" ] && [ -x "$PY_BIN" ] || die "新版主服务解释器不可用: $PY_BIN"
[ -n "$BLE_PY_BIN" ] && [ -x "$BLE_PY_BIN" ] || die "新版 BLE 解释器不可用: $BLE_PY_BIN"
PYTHONS=("$PY_BIN")
# 两个 unit 共用环境时只安装一次；将来分离解释器时分别安装。
[ "$BLE_PY_BIN" = "$PY_BIN" ] || PYTHONS+=("$BLE_PY_BIN")
for python_bin in "${PYTHONS[@]}"; do
    echo "  解释器: $python_bin"
    app_run timeout 600 "$python_bin" -m pip install --disable-pip-version-check --timeout 30 --retries 2 -r requirements.txt \
        || die "依赖安装失败或超时，尚未重启服务"
    app_run "$python_bin" -m pip check || die "依赖冲突，尚未重启服务"
done
# 元数据一致不代表二进制库可加载；预检另查运行库、RKNN/模型和 BLE 后端兼容性。
# 预检通过也不代表 NPU、摄像头和串口硬件已经可用，最终仍需现场业务验收。
app_run "$PY_BIN" deploy/upgrade_check.py --preflight || die "运行依赖/配置检查失败，尚未重启服务"
app_run "$BLE_PY_BIN" -c 'from src.ble.bless_compat import install_bluez_compat; assert install_bluez_compat()' \
    || die "BLE Python 环境不兼容，尚未重启服务"

step 4/6 "同步 service、日志、时间、OTG、热点与 BLE，并重启主服务"
# 已锁定 TARGET 并安装其依赖，禁止 update.sh 再 pull 到另一个提交。
DEPLOY_RC=0
# 保留配套部署失败状态；即使服务恢复，最终仍返回 2，避免把部分完成当作全部完成。
sudo_run bash update.sh --skip-pull || DEPLOY_RC=$?
if [ "$DEPLOY_RC" -ne 0 ] && [ "$DEPLOY_RC" -ne 2 ]; then
    sudo_run journalctl -u ai-dhlr.service -u ai-dhlr-ble.service -n 60 --no-pager || true
    die "update.sh 执行失败（退出码 $DEPLOY_RC）"
fi

step 5/6 "验证服务持续运行及应用版本"
# 单次 systemctl is-active 会碰到崩溃重启间隙；独立检查器同时观察 PID、重启次数与接口。
if ! app_run "$PY_BIN" deploy/upgrade_check.py; then
    sudo_run journalctl -u ai-dhlr.service -u ai-dhlr-ble.service -n 60 --no-pager || true
    die "升级验收失败"
fi

step 6/6 "升级结果"
echo "  系统版本: $(cat VERSION)"
echo "  Git 提交: $(app_run git rev-parse --short HEAD)"
if [ "$DEPLOY_RC" -ne 0 ]; then
    echo "[WARN] 主服务与 BLE 已通过启动检查，但配套部署存在失败，请查看以上输出"
    exit 2
fi
echo "==== 升级完成（启动检查通过，硬件功能仍需现场确认） ===="
REMOTE
} | "${SSH_CMD[@]}" 'IFS= read -r SUDO_PASS || exit 1; export SUDO_PASS; exec bash -s'
