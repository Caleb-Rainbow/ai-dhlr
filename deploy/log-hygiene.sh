#!/bin/bash
# 日志防膨胀一次性安装脚本（幂等，可随每次升级重复执行）
#
# 背景：设备镜像缺 logrotate 定时（timer/service 不存在、cron inactive），
# 曾有设备运行一年后 /var/log 撑满 14G 根分区（syslog 4.1G + kern.log 1.9G），
# 程序写配置报 Errno 28 全面故障。
#
# 本脚本安装三道防线：
#   1. logrotate 每日定时（镜像缺失，用 systemd timer 补上）
#   2. journald 磁盘配额 SystemMaxUse=200M（默认无限制可涨到文件系统 10%）
#   3. rsyslog 丢弃内核/WiFi/摄像头驱动的高频垃圾消息
#      （"not in Power Save mode" 年刷 500 万行、"rkcif update sensor info" 年刷
#        近千万行，与业务无关，纯驱动噪音）
#
# 要求 root 运行：sudo bash deploy/log-hygiene.sh
set -u

log() { echo "[log-hygiene] $*"; }

# ---------- 1. logrotate 每日定时 ----------
LOGROTATE_BIN=$(command -v logrotate || true)
if [ -z "$LOGROTATE_BIN" ]; then
    # 精简镜像常缺此包；设备能访问 github 一般也能访问 apt 源，尽力补装
    log "logrotate 未安装，尝试 apt-get install logrotate ..."
    apt-get install -y logrotate >/dev/null 2>&1 || log "[WARN] logrotate 安装失败（网络不通？），跳过"
    LOGROTATE_BIN=$(command -v logrotate || true)
fi

if [ -n "$LOGROTATE_BIN" ] && [ -f /etc/logrotate.conf ]; then
    # 包通常自带 /lib/systemd/system/logrotate.timer；仅当镜像缺单元文件时才自建
    if [ ! -f /lib/systemd/system/logrotate.timer ] && [ ! -f /usr/lib/systemd/system/logrotate.timer ]; then
        cat > /etc/systemd/system/logrotate.service <<EOF
[Unit]
Description=Rotate log files

[Service]
Type=oneshot
ExecStart=${LOGROTATE_BIN} /etc/logrotate.conf
EOF
        cat > /etc/systemd/system/logrotate.timer <<'EOF'
[Unit]
Description=Daily logrotate

[Timer]
OnCalendar=daily
AccuracySec=1h
Persistent=true

[Install]
WantedBy=timers.target
EOF
        systemctl daemon-reload
        log "已自建 logrotate 定时单元（镜像缺失）"
    fi
    systemctl enable --now logrotate.timer
    log "logrotate 每日定时已启用"
else
    log "[WARN] logrotate 不可用，跳过定时轮转（journald/rsyslog 防线仍生效）"
fi

# ---------- 2. journald 磁盘配额 ----------
mkdir -p /etc/systemd/journald.conf.d
cat > /etc/systemd/journald.conf.d/90-dhlr-size.conf <<'EOF'
# journald 默认无上限可涨到文件系统 10%（约 1.4G），限制到 200M
[Journal]
SystemMaxUse=200M
EOF
systemctl restart systemd-journald
journalctl --vacuum-size=200M >/dev/null 2>&1 || true
log "journald 已限制 SystemMaxUse=200M"

# ---------- 3. rsyslog 丢弃高频驱动噪音 ----------
# 需 rsyslog 在用才写过滤规则；文件放在 rsyslog.conf 头部 IncludeConfig 引入位置，先于默认落盘规则
if systemctl is-active --quiet rsyslog; then
    cat > /etc/rsyslog.d/01-dhlr-drop-spam.conf <<'EOF'
# 高频驱动噪音直接丢弃，不落盘（实测年积累：WiFi 电源模式 500 万行、
# rkcif 摄像头链路重试近千万行，撑爆 /var/log）
:msg, contains, "is not in Power Save mode" stop
:msg, contains, "No link between dphy and sensor" stop
:msg, contains, "rkcif_update_sensor_info" stop
:msg, contains, "update sensor info failed" stop
# OpenCV obsensor UVC 扫描警告（其他进程误触发时的兜底；主服务已用 OPENCV_LOG_LEVEL 抑制）
:msg, contains, "obsensor_stream_channel" stop
:msg, contains, "obsensor_uvc_stream_channel" stop
EOF
    systemctl restart rsyslog
    log "rsyslog 垃圾消息过滤已生效"
else
    log "[WARN] rsyslog 未运行，跳过过滤规则"
fi

log "完成"
