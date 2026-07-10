"""ai-dhlr 蓝牙配网服务入口（root，systemd unit ai-dhlr-ble.service）。

启动顺序：构建 device_info → NetworkApplier + DhlrClient → GattServer +
ProvisioningService 绑定 → 广播。阻塞主循环保持服务常驻。

运行：/home/linaro/miniforge3/envs/dhlr_env/bin/python -m src.ble
"""
from __future__ import annotations

import asyncio
import logging
import re
import subprocess
import sys
from pathlib import Path

from .dhlr_bridge import DhlrBridge
from .dhlr_client import DhlrClient
from .gatt_server import GattServer
from .network_applier import NetworkApplier
from .patrol_notify import _ensure_patrol_frame_safe, _patrol_notify
from .service import ProvisioningService

logger = logging.getLogger("ble")

_MAC_RE = re.compile(r"((?:[0-9A-Fa-f]{2}:){5}[0-9A-Fa-f]{2})")


def _bt_address() -> str:
    """取 hci0 蓝牙地址（sysfs → hciconfig → btmgmt 多级回退）。"""
    # 1) sysfs
    try:
        p = Path("/sys/class/bluetooth/hci0/address")
        if p.exists():
            v = p.read_text().strip().upper()
            if _MAC_RE.fullmatch(v):
                return v
    except Exception:
        pass
    # 2) hciconfig / btmgmt
    for cmd in (["hciconfig", "hci0"], ["btmgmt", "info"]):
        try:
            out = subprocess.run(cmd, capture_output=True, text=True, timeout=5).stdout
            m = _MAC_RE.search(out)
            if m:
                return m.group(1).upper()
        except Exception:
            continue
    return "00:00:00:00:00:00"


def _load_fw_name_device_id() -> tuple[str, str, str]:
    try:
        from src.utils.config import config_manager

        cfg = config_manager.config
        return (
            cfg.system.version or "0.0.0",
            cfg.system.name or "AI动火离人",
            getattr(cfg.system, "device_id", "") or "",
        )
    except Exception as e:  # noqa: BLE001
        logger.warning("读取 dhlr 配置失败，使用默认值: %s", e)
        return "0.0.0", "AI动火离人", ""


async def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    bt = _bt_address()
    fw, friendly, device_id = _load_fw_name_device_id()
    hwid = bt.replace(":", "-").lower()
    suffix = bt.replace(":", "")[-4:]

    identity = {"type": "dhlr", "model": "ai-dhlr", "fw": fw, "hwid": hwid, "name": friendly}
    # 广播名 = 设备ID（约定以 AILRBJ 开头，如 AILRBJ26032502）：手机按前缀匹配并区分多台设备；
    # 设备ID 缺失时回退到 MAC 后缀（仅未配网/异常兜底，此时不被新前缀匹配）。
    adv_name = device_id.strip() or f"AI-DHLR-{suffix}"

    network = NetworkApplier()
    dhlr = DhlrClient()
    gatt = GattServer(identity=identity, name=adv_name)

    async def on_broadcast(msg: dict) -> None:
        # 转发设备主动广播（alarm_event / patrol_event）经 STATUS 通知给 App。
        # base64 图片已在主应用侧剥离（BLE 帧 payload 上限 8KB，图片经 LAN/云通道获取）。
        mtype = msg.get("type")
        if mtype == "alarm_event":
            data = msg.get("data") or {}
            await gatt.notify({
                "event": "alarm",
                "id": 0,
                "zone_id": data.get("zone_id"),
                "zone_name": data.get("zone_name"),
                "alarm_type": data.get("alarm_type"),
                "message": data.get("message"),
            })
        elif mtype == "patrol_event":
            # 巡检进度/结果流（设备后台线程推送）：整态替换，超帧裁剪兜底。
            await gatt.notify(_ensure_patrol_frame_safe(_patrol_notify(msg.get("data") or {})))

    bridge = DhlrBridge(on_broadcast=on_broadcast)
    service = ProvisioningService(network, dhlr, gatt.notify, bridge=bridge)
    gatt.bind_service(service)

    bridge.start()
    await gatt.start()
    logger.info("BLE 配网服务就绪: %s (hwid=%s, fw=%s)", adv_name, hwid, fw)

    while True:
        await asyncio.sleep(3600)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        sys.exit(0)
