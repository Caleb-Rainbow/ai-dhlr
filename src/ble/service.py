"""配网状态机编排：命令 → 动作 → STATUS 通知。

ProvisioningService 把 BLE 收到的命令（scan_wifi/set_config/apply/cancel）
分派到 network_applier + dhlr_client，按状态机经 notify 下发 STATUS 事件。
阻塞的 nmcli/httpx 调用用 asyncio.to_thread 包裹，不阻塞 bless 事件循环。
"""
from __future__ import annotations

import asyncio
import logging
from typing import Awaitable, Callable, List, Optional

from .dhlr_client import DhlrClient
from .network_applier import NetworkApplier

logger = logging.getLogger(__name__)

# 状态机状态（与协议文档一致）
IDLE = "IDLE"
SCANNING = "SCANNING"
APPLYING = "APPLYING"
CONNECTING = "CONNECTING"
CONNECTED = "CONNECTED"
FAILED = "FAILED"

# notify(obj_dict) —— 由 GattServer 提供：分配 seq、分帧、写 STATUS 特征值通知
Notify = Callable[[dict], Awaitable[None]]


class ProvisioningService:
    def __init__(
        self,
        network: NetworkApplier,
        dhlr: DhlrClient,
        notify: Notify,
    ) -> None:
        self._network = network
        self._dhlr = dhlr
        self._notify = notify
        self._state = IDLE
        self._pending_config: Optional[dict] = None
        self._lock = asyncio.Lock()

    @property
    def state(self) -> str:
        return self._state

    # ---------------------------- 通知辅助 ---------------------------- #
    async def _state_update(self, cid: int, state: str, **extra) -> None:
        obj = {"event": "state_update", "id": cid, "state": state}
        obj.update(extra)
        self._state = state
        await self._notify(obj)

    async def _scan_results(self, cid: int, networks: List[dict]) -> None:
        await self._notify({"event": "scan_results", "id": cid, "networks": networks})

    async def _error(self, cid: int, code: str, message: str = "") -> None:
        await self._notify({"event": "error", "id": cid, "code": code, "message": message})

    # ---------------------------- 命令分派 ---------------------------- #
    async def handle(self, seq: int, command: dict) -> None:
        async with self._lock:
            cid = command.get("id", seq)
            cmd = command.get("cmd")
            logger.info(f"[cmd] seq={seq} cid={cid} cmd={cmd}")
            try:
                if cmd == "scan_wifi":
                    await self._do_scan(cid)
                elif cmd == "set_config":
                    await self._do_set_config(cid, command.get("config", {}))
                elif cmd == "apply":
                    await self._do_apply(cid)
                elif cmd == "cancel":
                    await self._do_cancel(cid)
                elif cmd == "get_config":
                    await self._do_get_config(cid)
                else:
                    await self._error(cid, "bad_request", f"unknown cmd: {cmd}")
            except Exception as e:  # noqa: BLE001
                logger.exception("命令处理异常 cmd=%s", cmd)
                await self._error(cid, "internal", str(e))

    async def _do_scan(self, cid: int) -> None:
        if self._state not in (IDLE, SCANNING):
            await self._error(cid, "busy", f"state={self._state}")
            return
        await self._state_update(cid, SCANNING)
        logger.info(f"[scan] cid={cid} 开始 nmcli 扫描")
        networks = await asyncio.to_thread(self._network.scan)
        logger.info(f"[scan] cid={cid} 扫到 {len(networks)} 个 AP，发送 scan_results")
        await self._scan_results(cid, networks)
        await self._state_update(cid, IDLE)

    async def _do_set_config(self, cid: int, config: dict) -> None:
        if not isinstance(config, dict):
            await self._error(cid, "bad_request", "config must be object")
            return
        self._pending_config = config
        await self._state_update(cid, IDLE, config_ack=True)

    async def _do_apply(self, cid: int) -> None:
        cfg = self._pending_config
        if not cfg:
            await self._error(cid, "bad_request", "no config; send set_config first")
            return

        await self._state_update(cid, APPLYING)

        # 1) 应用 remote/system 给 dhlr（localhost，不影响 BLE）
        remote = cfg.get("remote")
        system = cfg.get("system")
        if remote or system:
            r = await asyncio.to_thread(self._dhlr.apply_config, remote, system)
            if not r.get("ok"):
                await self._error(cid, "apply_failed", r.get("error", "dhlr apply failed"))
                await self._state_update(cid, IDLE)
                return

        # 2) WiFi 切网
        wifi = cfg.get("wifi") or {}
        ssid = wifi.get("ssid")
        if ssid:
            await self._state_update(cid, CONNECTING)
            r = await asyncio.to_thread(self._network.connect, ssid, wifi.get("password", ""))
            if not r.get("ok"):
                await self._error(cid, "wifi_connect_failed", r.get("error", "connect failed"))
                await self._state_update(cid, IDLE)
                return
            await self._state_update(cid, CONNECTED, ip=r.get("ip"), ssid=ssid)
        else:
            await self._state_update(cid, CONNECTED)

        self._pending_config = None

    async def _do_cancel(self, cid: int) -> None:
        # nmcli 切网难以中途取消；v1 仅在空闲时回到 IDLE，否则报 busy
        if self._state == IDLE:
            self._pending_config = None
            await self._state_update(cid, IDLE)
        else:
            await self._error(cid, "busy", f"cannot cancel state={self._state}")

    async def _do_get_config(self, cid: int) -> None:
        # 返回设备当前 remote/system 配置（读 config.yaml），供 App 回填表单
        config: dict = {}
        try:
            from src.utils.config import config_manager
            cfg = config_manager.config
            config = {
                "remote": {
                    "enabled": cfg.remote.enabled,
                    "server_url": cfg.remote.server_url,
                    "token": cfg.remote.token,
                },
                "system": {
                    "name": cfg.system.name,
                    "device_id": cfg.system.device_id,
                },
            }
        except Exception as e:
            logger.warning(f"读取当前配置失败: {e}")
        await self._notify({"event": "current_config", "id": cid, "config": config})
