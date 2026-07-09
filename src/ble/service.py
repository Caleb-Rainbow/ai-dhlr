"""配网状态机编排：命令 → 动作 → STATUS 通知。

ProvisioningService 把 BLE 收到的命令（scan_wifi/set_config/apply/cancel）
分派到 network_applier + dhlr_client，按状态机经 notify 下发 STATUS 事件。
阻塞的 nmcli/httpx 调用用 asyncio.to_thread 包裹，不阻塞 bless 事件循环。
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
from typing import Awaitable, Callable, List, Optional

from .dhlr_client import DhlrClient
from .network_applier import NetworkApplier
from .protocol import MAX_FRAME_PAYLOAD

logger = logging.getLogger(__name__)

# BLE rpc 白名单（无鉴权，按「操作台需要 + 危险动作靠 App 侧 Destructive 二次确认」原则放行）。
# 明确不放行：trigger_update（git reset+重启，OTA 级）、install_dependencies（pip）、
# toggle_fire（调试）、硬件配置（serial/gpio/usb_otg）、zone/camera CRUD、remote 账密——
# 这些非操作台日常动作，或参数会撞 BLE 8KB 帧上限。
RPC_ACTION_WHITELIST = frozenset({
    # 只读查询
    "get_status", "get_device", "get_performance", "get_network",
    # 运行态设置读写（④配置）
    "get_settings", "update_settings",
    "get_zone_mode", "set_zone_mode",
    "get_volume", "set_volume",
    # 控制动作（③动作）；强制类由 App ConfirmLevel.Destructive 二次确认
    "patrol_self_check", "start_patrol", "stop_patrol", "patrol_alarm_demo",
    "patrol_force_warning", "patrol_force_alarm", "patrol_force_cutoff",
})

# 图像多帧流式：单帧 payload 上限 8192，整图（告警快照 base64 数十 KB）必切块。
# 每块 JSON 信封（event/id/index/data，最坏 ~70B）+ base64 切片须 < 8192；base64 为 ASCII 无扩展，
# 6000 切片 + 信封 ≈ 6100B，距 8192 留 >2KB 余裕（实测最坏帧 6105B）。
IMAGE_CHUNK_SIZE = 6000

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
        bridge=None,
    ) -> None:
        self._network = network
        self._dhlr = dhlr
        self._notify = notify
        self._bridge = bridge  # DhlrBridge，可选：未注入时 rpc 返回 unsupported
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
        cid = command.get("id", seq)
        cmd = command.get("cmd")
        # rpc 不进配网状态机锁：查询可与扫网/切网并行，且桥超时不阻塞配网命令
        if cmd == "rpc":
            logger.info(f"[cmd] seq={seq} cid={cid} cmd=rpc action={command.get('action')}")
            try:
                await self._do_rpc(cid, command)
            except Exception as e:  # noqa: BLE001
                logger.exception("rpc 处理异常")
                await self._error(cid, "internal", str(e))
            return
        # get_image 同样不进锁：取图经桥，可与配网并行；图像以多帧 image_* 流式 notify
        if cmd == "get_image":
            logger.info(f"[cmd] seq={seq} cid={cid} cmd=get_image file={command.get('filename')}")
            try:
                await self._do_get_image(cid, command)
            except Exception as e:  # noqa: BLE001
                logger.exception("get_image 处理异常")
                await self._notify({"event": "image_error", "id": cid, "error": str(e)})
            return
        # get_preview 抓实时帧（复用 image_* 多帧通道），同样不进锁
        if cmd == "get_preview":
            logger.info(f"[cmd] seq={seq} cid={cid} cmd=get_preview cam={command.get('camera_id')}")
            try:
                await self._do_get_preview(cid, command)
            except Exception as e:  # noqa: BLE001
                logger.exception("get_preview 处理异常")
                await self._notify({"event": "image_error", "id": cid, "error": str(e)})
            return
        async with self._lock:
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
        # 扫描 WiFi 是只读操作(nmcli scan)，不干扰现有连接；仅在正在切网(APPLYING/CONNECTING)时拒绝。
        # 必须允许 CONNECTED 状态扫描——否则已联网设备重新配网时首个 scan_wifi 即 busy 失败。
        if self._state in (APPLYING, CONNECTING):
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

    async def _do_rpc(self, cid: int, command: dict) -> None:
        """把白名单内的 action（只读查询 / 运行态设置 / 控制动作）经 DhlrBridge 转发给主应用，
        结果以 rpc_result 回帧。控制类动作的安全性由 App 侧二次确认兜底（BLE 无鉴权）。"""
        action = command.get("action")
        if not isinstance(action, str) or action not in RPC_ACTION_WHITELIST:
            await self._error(cid, "bad_request", f"action not allowed: {action}")
            return
        if self._bridge is None:
            await self._error(cid, "unsupported", "rpc bridge not available")
            return
        params = command.get("params")
        result = await self._bridge.request(action, params if isinstance(params, dict) else {})
        result_obj = {
            "event": "rpc_result",
            "id": cid,
            "action": action,
            "success": result.get("success", False),
            "data": result.get("data"),
            "error": result.get("error"),
        }
        # 响应过大（超单帧 8192 上限）：接收端会静默丢帧致 App rpc 等待器超时。
        # 主动检测并回紧凑错误，让对端拿到明确失败而非超时。
        if len(json.dumps(result_obj, ensure_ascii=False).encode("utf-8")) > MAX_FRAME_PAYLOAD:
            logger.warning(f"[rpc] cid={cid} action={action} 响应超帧上限，截断为错误")
            result_obj = {"event": "rpc_result", "id": cid, "action": action,
                          "success": False, "error": "response too large"}
        await self._notify(result_obj)

    def _image_data_uri(self, resp: dict) -> Optional[str]:
        """从桥响应取 image 字段（data URI 或裸 base64）。"""
        data = resp.get("data")
        return data.get("image") if isinstance(data, dict) else None

    async def _stream_image(self, cid: int, image: Optional[str]) -> None:
        """把图像（data URI 或裸 base64）剥前缀→按 IMAGE_CHUNK_SIZE 切块→
        image_start/image_chunk/image_end 流式 notify。告警快照与实时预览共用。"""
        if not isinstance(image, str) or not image:
            await self._notify({"event": "image_error", "id": cid, "error": "no image data"})
            return
        # 剥 "data:<mime>;base64," 前缀，保留 mime
        mime, b64 = "image/jpeg", image
        if image.startswith("data:") and ";base64," in image:
            head, b64 = image.split(";base64,", 1)
            m = head[5:]  # 去 "data:"
            if m.startswith("image/"):
                mime = m
        if not b64:
            await self._notify({"event": "image_error", "id": cid, "error": "empty image data"})
            return
        chunks = [b64[i:i + IMAGE_CHUNK_SIZE] for i in range(0, len(b64), IMAGE_CHUNK_SIZE)]
        logger.info(f"[image] cid={cid} mime={mime} bytes={len(b64)} chunks={len(chunks)}")
        await self._notify({"event": "image_start", "id": cid,
                            "mime": mime, "total_chunks": len(chunks)})
        for idx, chunk in enumerate(chunks):
            await self._notify({"event": "image_chunk", "id": cid, "index": idx, "data": chunk})
        await self._notify({"event": "image_end", "id": cid})

    async def _do_get_image(self, cid: int, command: dict) -> None:
        """按需取告警快照：经桥 get_snapshot 取整图 → _stream_image 多帧 notify。

        filename 是 BLE wire 输入（BLE 无鉴权，中心端可下发任意值），**不可信**：
        仅取 basename 并校验，防 `../../etc/shadow` 类路径穿越读任意文件（主应用 root）。
        合法值是设备生成的 last_snapshot_path（snapshots/ 下文件），basename 后即文件名。"""
        raw = command.get("filename")
        # 仅取 basename：把任意路径/穿越尝试收敛到单一文件名（主应用侧 _get_snapshot_image 再做 relative_to 兜底）
        filename = os.path.basename(raw) if isinstance(raw, str) else ""
        if not filename or filename in (".", "..") or filename.startswith("."):
            # 发 image_error（非 _error）：App 的 awaitImage 只认 image_*，否则等待器挂 30s 超时
            await self._notify({"event": "image_error", "id": cid, "error": "invalid filename"})
            return
        if self._bridge is None:
            await self._notify({"event": "image_error", "id": cid, "error": "rpc bridge not available"})
            return
        resp = await self._bridge.request("get_snapshot", {"filename": filename})
        if not resp.get("success"):
            await self._notify({"event": "image_error", "id": cid,
                                "error": resp.get("error") or "snapshot fetch failed"})
            return
        await self._stream_image(cid, self._image_data_uri(resp))

    async def _do_get_preview(self, cid: int, command: dict) -> None:
        """抓取指定摄像头的当前实时帧：经桥 preview_camera 取帧 → _stream_image 多帧 notify。
        camera_id 来自 get_status 的 zone.camera_id（设备配置，可信）。摄像头离线时桥返回失败→image_error。"""
        camera_id = command.get("camera_id")
        if not isinstance(camera_id, str) or not camera_id:
            # 发 image_error（非 _error）：App 的 awaitImage 只认 image_*，否则等待器挂 30s 超时
            await self._notify({"event": "image_error", "id": cid, "error": "missing camera_id"})
            return
        if self._bridge is None:
            await self._notify({"event": "image_error", "id": cid, "error": "rpc bridge not available"})
            return
        resp = await self._bridge.request("preview_camera", {"camera_id": camera_id})
        if not resp.get("success"):
            await self._notify({"event": "image_error", "id": cid,
                                "error": resp.get("error") or "preview failed"})
            return
        await self._stream_image(cid, self._image_data_uri(resp))

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
