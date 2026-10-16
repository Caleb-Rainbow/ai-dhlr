#!/bin/bash
# 设备侧部署入口：默认先 git pull；手动升级入口已锁定提交时传 --skip-pull。
# 本脚本不安装 Python 依赖，也不执行持续运行/接口验收；完整流程由
# deploy/upgrade-old-device.sh 调度，验收标准见其文件头与 README。
# BLE 在主服务之前部署并启动，兼容旧更新进程可能随主服务重启被终止的场景。
# 退出码：0 部署成功；1 硬错误；2 配套部署失败，但仍尝试重启主服务。
set -euo pipefail
die() { echo "[FAIL] $*" >&2; exit 1; }
SCRIPT_DIR="$(cd -- "$(dirname -- "$0")" && pwd)"
cd "$SCRIPT_DIR" || exit 1
SKIP_PULL=0
case "${1:-}" in
    '') ;;
    --skip-pull) SKIP_PULL=1 ;;
    *) die "用法: bash update.sh [--skip-pull]" ;;
esac
[ "$#" -le 1 ] || die "参数过多"

echo "开始执行更新程序..."
# Git 始终以仓库运行账号执行，防止 root 污染对象库属主。
run_git() {
    if [ "$(id -u)" -eq 0 ]; then
        runuser -u linaro -- env HOME=/home/linaro git "$@"
    else
        git "$@"
    fi
}
if [ "$SKIP_PULL" -eq 0 ]; then
    SELF_HASH_BEFORE=$(sha256sum update.sh | cut -d' ' -f1)
    run_git pull || die "代码更新失败，请检查网络或 Git 配置"
    SELF_HASH_AFTER=$(sha256sum update.sh | cut -d' ' -f1)
    if [ "$SELF_HASH_BEFORE" != "$SELF_HASH_AFTER" ]; then
        echo "update.sh 自身已更新，重新执行新版..."
        exec bash "$SCRIPT_DIR/update.sh" "$@"
    fi
fi

echo "正在同步部署配置..."
[ -f deploy/ai-dhlr.service ] || die "缺少 deploy/ai-dhlr.service"
if ! sudo cmp -s deploy/ai-dhlr.service /etc/systemd/system/ai-dhlr.service; then
    sudo cp deploy/ai-dhlr.service /etc/systemd/system/ai-dhlr.service || die "同步主服务 unit 失败"
    sudo systemctl daemon-reload || die "systemd daemon-reload 失败"
    echo "主服务配置已更新"
fi

# 辅助步骤失败后仍恢复主服务，但最终必须报告部分失败，不能被末尾命令覆盖。
DEPLOY_RC=0
for script in log-hygiene.sh time-sync.sh usb-otg-persist.sh bootstrap-hotspot.sh bootstrap-ble.sh; do
    if [ ! -f "deploy/$script" ] || ! sudo bash "deploy/$script"; then
        echo "[WARN] deploy/$script 部署失败，继续恢复主服务"
        DEPLOY_RC=2
    fi
done

echo "正在重启主服务..."
sudo systemctl restart ai-dhlr.service || die "主服务重启失败"
# bootstrap-ble.sh 已重启 BLE；其 unit 不依赖主服务的运行状态，无需重复重启。
if [ "$DEPLOY_RC" -ne 0 ]; then
    echo "[WARN] 部署部分失败，请修复上述步骤后重试"
fi
exit "$DEPLOY_RC"
