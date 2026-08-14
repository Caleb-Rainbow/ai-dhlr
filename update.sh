#!/bin/bash

# 进入项目目录
cd /home/linaro/ai-dhlr

echo "开始执行更新程序..."

# 1. 强制放弃本地修改，确保与远程仓库同步（防止因本地误操作导致 pull 失败）
# 如果你想保留本地修改，请把下面两行换成 git pull
git pull

# 2. 检查上一步是否成功
if [ $? -eq 0 ]; then
    echo "代码拉取成功，正在同步配置..."

    # 幂等同步主服务配置：仓库 deploy/ai-dhlr.service 与设备不一致时才更新
    # 用于随 git 下发 systemd 配置变更（如 MALLOC_ARENA_MAX=2）
    # 注意：这会让仓库成为 service 的唯一来源，覆盖设备本地的手动修改
    if [ -f deploy/ai-dhlr.service ] && ! sudo cmp -s deploy/ai-dhlr.service /etc/systemd/system/ai-dhlr.service; then
        sudo cp deploy/ai-dhlr.service /etc/systemd/system/ai-dhlr.service
        sudo systemctl daemon-reload
        echo "主服务配置已更新（daemon-reload）"
    fi

    echo "正在重启服务..."
    # 重启 Python 服务
    sudo systemctl restart ai-dhlr.service
    # 重启蓝牙配网服务（未安装/未启用时忽略）
    sudo systemctl try-restart ai-dhlr-ble.service 2>/dev/null || true
else
    echo "代码更新失败，请检查网络或 Git 配置。"
    exit 1
fi