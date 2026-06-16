"""ai-dhlr 蓝牙配网服务入口（root，systemd unit ai-dhlr-ble.service）。

启动顺序：构建 device_info → NetworkApplier + DhlrClient → GattServer +
ProvisioningService 绑定 → 广播。阻塞主循环保持服务常驻。

运行：/home/linaro/miniforge3/envs/dhlr_env/bin/python -m src.ble
"""
from __future__ import annotations

import asyncio
import logging
import sys
from pathlib import Path

from . import protocol as P
from .dhlr_client import DhlrClient
from .gatt_server import GattServer
from .network_applier import NetworkApplier
from .service import ProvisioningService

logger = logging.getLogger("ble")


def _bt_address() -> str:
    try:
        return Path("/sys/class/bluetooth/hci0/address").read_text().strip().upper()
    except Exception:
        return "00:00:00:00:00:00"


def _load_fw_and_name() -> tuple[str, str]:
    try:
        from src.utils.config import config_manager

        cfg = config_manager.config
        return cfg.system.version or "0.0.0", cfg.system.name or "AI动火离人"
    except Exception as e:  # noqa: BLE001
        logger.warning("读取 dhlr 配置失败，使用默认值: %s", e)
        return "0.0.0", "AI动火离人"


async def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    bt = _bt_address()
    fw, friendly = _load_fw_and_name()
    hwid = bt.replace(":", "-").lower()
    suffix = bt.replace(":", "")[-4:]

    device_info = P.build_device_info(
        type="dhlr", model="ai-dhlr", fw=fw, hwid=hwid, name=friendly
    )
    adv_name = f"AI-DHLR-{suffix}"

    network = NetworkApplier()
    dhlr = DhlrClient()
    gatt = GattServer(device_info_bytes=device_info, name=adv_name)
    service = ProvisioningService(network, dhlr, gatt.notify)
    gatt.bind_service(service)

    await gatt.start()
    logger.info("BLE 配网服务就绪: %s (hwid=%s, fw=%s)", adv_name, hwid, fw)

    while True:
        await asyncio.sleep(3600)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        sys.exit(0)
