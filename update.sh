#!/bin/bash

# 进入项目目录
cd /home/linaro/ai-dhlr

echo "开始执行更新程序..."

# 1. 记录 update.sh 自身指纹（用于自更新保护）
SELF_HASH_BEFORE=$(sha256sum update.sh 2>/dev/null | cut -d' ' -f1)

# 2. 拉取最新代码
git pull
if [ $? -ne 0 ]; then
    echo "代码更新失败，请检查网络或 Git 配置。"
    exit 1
fi

# 3. 自更新保护：若 update.sh 在本次 pull 中被改动，重新执行新版
#    原因：bash 一次性把脚本读入内存逐行执行，运行中 git pull 更新了磁盘上的
#    update.sh，但当前进程仍跑旧版，新增逻辑这次不会执行。必须显式 exec 切到新版。
SELF_HASH_AFTER=$(sha256sum update.sh 2>/dev/null | cut -d' ' -f1)
if [ "$SELF_HASH_BEFORE" != "$SELF_HASH_AFTER" ]; then
    echo "update.sh 自身已更新，重新执行新版..."
    exec ./update.sh
fi

echo "代码拉取成功，正在同步配置..."

# 4. 幂等同步主服务配置：仓库 deploy/ai-dhlr.service 与设备不一致时才更新
#    用于随 git 下发 systemd 配置变更（如 MALLOC_ARENA_MAX=2）
#    注意：这会让仓库成为 service 的唯一来源，覆盖设备本地的手动修改
if [ -f deploy/ai-dhlr.service ] && ! sudo cmp -s deploy/ai-dhlr.service /etc/systemd/system/ai-dhlr.service; then
    sudo cp deploy/ai-dhlr.service /etc/systemd/system/ai-dhlr.service
    sudo systemctl daemon-reload
    echo "主服务配置已更新（daemon-reload）"
fi

# 5. 日志防膨胀：logrotate 定时 + journald 上限 + rsyslog 垃圾过滤（幂等）
#    曾有设备运行一年日志撑满根分区导致写配置 Errno 28 故障
if [ -f deploy/log-hygiene.sh ]; then
    sudo bash deploy/log-hygiene.sh
fi

# 6. 时间同步：chrony（或 ntpdate 退路），镜像自带的时间服务全是坏的，
#    RTC 无电池断电丢时间，时钟可偏差一年以上
if [ -f deploy/time-sync.sh ]; then
    sudo bash deploy/time-sync.sh
fi

echo "正在重启服务..."
# 重启 Python 服务
sudo systemctl restart ai-dhlr.service
# 重启蓝牙配网服务（未安装/未启用时忽略）
sudo systemctl try-restart ai-dhlr-ble.service 2>/dev/null || true
