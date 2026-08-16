#!/bin/bash
# 时间同步安装脚本（幂等，可随每次升级重复执行）
#
# 背景：设备镜像的 systemd-timesyncd 启动失败（NAMESPACE 沙箱错误）、
# ntp.service 符号链接断裂、ntpd 未运行，三套机制全废；RK808 RTC 无备用
# 电池断电不保持。实测设备时钟可偏差一年以上，导致：
#   - 日志/快照时间戳全错，事件无法对账
#   - 按文件名日期的日志清理失效（disk_guard 已改按 mtime 兜底）
#   - TLS/JWT 等依赖时间的功能不可用
#
# 方案：优先 chrony（大偏差直接步进 makestep、断网容忍好、rtcsync 持续回写
# RTC）；安装失败则退回 ntpsec 自带 ntpdate 的 oneshot + timer。
#
# 要求 root 运行：sudo bash deploy/time-sync.sh
set -u

NTP_SERVERS=(ntp.aliyun.com ntp.tencent.com cn.pool.ntp.org)

log() { echo "[time-sync] $*"; }

# 停用镜像里残留的坏时间服务，避免与 chrony 抢时钟
for u in systemd-timesyncd ntp ntpd ntpsec-ntpd; do
    systemctl stop "$u" >/dev/null 2>&1 || true
    systemctl disable "$u" >/dev/null 2>&1 || true
done

# ---------- 方案1: chrony ----------
if command -v chronyd >/dev/null 2>&1 || apt-get install -y chrony >/dev/null 2>&1; then
    conf_dir=/etc/chrony
    [ -d "$conf_dir" ] || conf_dir=/etc
    cat > "$conf_dir/chrony.conf" <<EOF
# DHLR 设备时间同步（deploy/time-sync.sh 生成，勿手改，升级会覆盖）
$(printf 'server %s iburst minpoll 4 maxpoll 6\n' "${NTP_SERVERS[@]}")
driftfile /var/lib/chrony/chrony.drift

# 开机时钟可能是任意值（RTC 无电池），任何时刻偏差>1s 都直接步进
makestep 1 -1

# 同步后持续把系统时间回写 RTC，供电期间断重启也不丢
rtcsync
EOF
    mkdir -p /var/lib/chrony
    systemctl enable --now chrony >/dev/null 2>&1 \
        || systemctl restart chrony >/dev/null 2>&1 || true
    # 等首次同步（步进发生）
    for _ in $(seq 1 12); do
        if chronyc tracking 2>/dev/null | grep -q 'Leap status.*Normal'; then
            log "chrony 已同步: $(date '+%F %T')"
            chronyc tracking | grep -E 'System time|Last offset' | sed 's/^/[time-sync]   /'
            log "完成（chrony）"
            exit 0
        fi
        sleep 5
    done
    log "[WARN] chrony 已启用但 60s 内未完成首次同步（网络不通？），等待后台重试"
    log "完成（chrony，待同步）"
    exit 0
fi

# ---------- 方案2: ntpdate oneshot + timer（chrony 装不上时） ----------
log "chrony 不可用，退回 ntpdate 方案"
NTPDATE_BIN=$(command -v ntpdate || echo /usr/sbin/ntpdate)
if [ ! -x "$NTPDATE_BIN" ]; then
    log "[WARN] ntpdate 也不可用，时间同步配置失败"
    exit 1
fi

cat > /usr/local/sbin/dhlr-ntpdate.sh <<EOF
#!/bin/bash
# 依次尝试多个 NTP 源，成功后回写 RTC
for s in ${NTP_SERVERS[*]}; do
    if "$NTPDATE_BIN" -b "\$s" >/dev/null 2>&1; then
        hwclock --systohc 2>/dev/null || true
        echo "dhlr-ntpdate: 已同步到 \$s"
        exit 0
    fi
done
exit 1
EOF
chmod +x /usr/local/sbin/dhlr-ntpdate.sh

cat > /etc/systemd/system/dhlr-timesync.service <<'EOF'
[Unit]
Description=DHLR one-shot NTP time sync
Wants=network-online.target
After=network-online.target

[Service]
Type=oneshot
ExecStart=/usr/local/sbin/dhlr-ntpdate.sh
# 开机网络就绪有先后，失败自动重试
Restart=on-failure
RestartSec=30
EOF

cat > /etc/systemd/system/dhlr-timesync.timer <<'EOF'
[Unit]
Description=Periodic DHLR NTP sync

[Timer]
OnBootSec=1min
OnUnitActiveSec=30min

[Install]
WantedBy=timers.target
EOF

systemctl daemon-reload
systemctl enable --now dhlr-timesync.timer
systemctl start dhlr-timesync.service || true
log "完成（ntpdate + timer）"
