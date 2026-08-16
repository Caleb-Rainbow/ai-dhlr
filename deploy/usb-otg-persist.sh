#!/bin/bash
# USB OTG 模式持久化安装脚本（幂等，可随每次升级重复执行）
#
# 背景：主服务切 OTG 模式是直接写 sysfs（fe8a0000.usb2-phy/otg_mode），sysfs 为
# 内存态，重启后回出厂 peripheral；且开机后 USB 子系统（dwc3/usb2phy 角色管理）
# 会反复把 otg_mode 重置回 peripheral——实测开机一次性回放（oneshot，含确认
# 循环）也会在判定稳定之后被晚到的覆盖改回，单次/有限次回写均不可靠。
#
# 方案：用户在界面切换时，主服务把模式写入 /var/lib/dhlr/usb_otg_mode（见
# ws_handler._set_usb_otg_mode）；本脚本安装 dhlr-usb-otg-restore.service（常驻
# 守护），每 15s 比对 sysfs 与持久文件，不一致即纠正。守护每轮重读持久文件，
# 用户切换后无需重启服务即自动跟随。纠正动作输出到 journal 便于排查。
#
# 安全边界：文件不存在（用户从未显式切换）或值异常时守护不写 sysfs，保持
# 出厂默认 peripheral——OTG 口兼任调试口，绝不因升级/损坏而默认锁定 host。
#
# 要求 root 运行：sudo bash deploy/usb-otg-persist.sh
set -u

log() { echo "[usb-otg] $*"; }

cat > /usr/local/sbin/dhlr-usb-otg-keeper.sh <<'EOF'
#!/bin/bash
# USB OTG 模式守护（dhlr-usb-otg-restore.service 调用，常驻）
PERSIST_FILE=/var/lib/dhlr/usb_otg_mode
SYSFS=/sys/devices/platform/fe8a0000.usb2-phy/otg_mode

while true; do
    # 每轮重读持久文件：跟随用户最新选择，也天然容忍文件暂时不可读
    MODE=$(cat "$PERSIST_FILE" 2>/dev/null)
    case "$MODE" in
        host|peripheral)
            if [ -e "$SYSFS" ] && [ "$(cat "$SYSFS" 2>/dev/null)" != "$MODE" ]; then
                if echo "$MODE" > "$SYSFS" 2>/dev/null; then
                    echo "$(date '+%F %T') otg_mode 已纠正为 $MODE"
                else
                    echo "$(date '+%F %T') 写入 $SYSFS 失败"
                fi
            fi
            ;;
        # 无记录/值异常：不动作，保持出厂默认
    esac
    sleep 15
done
EOF
chmod 755 /usr/local/sbin/dhlr-usb-otg-keeper.sh

cat > /etc/systemd/system/dhlr-usb-otg-restore.service <<'EOF'
[Unit]
Description=DHLR USB OTG mode keeper
# 守护用户上次显式选择的 OTG 模式；无记录则不动作，保持出厂默认

[Service]
Type=simple
ExecStart=/usr/local/sbin/dhlr-usb-otg-keeper.sh
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
systemctl enable dhlr-usb-otg-restore.service >/dev/null 2>&1
# 升级场景：立即以新脚本重启守护（首次安装=启动）
systemctl restart dhlr-usb-otg-restore.service
log "完成：OTG 模式守护已启用（15s 周期，/var/lib/dhlr/usb_otg_mode → sysfs）"
