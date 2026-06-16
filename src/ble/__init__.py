"""ai-dhlr 蓝牙配网服务（root，独立 systemd 单元 ai-dhlr-ble.service）。

与 dhlr 主程序解耦：仅做 BLE GATT + WiFi 切网，应用配置经 dhlr 的
localhost /internal/apply-provisioning 接口下发。见 docs/ble-provisioning-protocol.md。
"""
