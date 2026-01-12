#!/bin/bash

# 进入项目目录
cd /home/linaro/ai-dhlr

echo "开始执行更新程序..."

# 1. 强制放弃本地修改，确保与远程仓库同步（防止因本地误操作导致 pull 失败）
# 如果你想保留本地修改，请把下面两行换成 git pull
git pull

# 2. 检查上一步是否成功
if [ $? -eq 0 ]; then
    echo "代码拉取成功，正在重启服务..."
    # 重启 Python 服务
    sudo systemctl restart ai-dhlr.service
else
    echo "代码更新失败，请检查网络或 Git 配置。"
    exit 1
fi