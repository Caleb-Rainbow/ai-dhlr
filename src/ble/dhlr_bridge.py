"""dhlr 主应用 WS 桥（BLE root 进程 → 127.0.0.1:8000/ws/status）。

一条本机 WebSocket 长连接承担两件事：
1. 订阅主应用广播（alarm_event 等），经回调转发给 GATT STATUS 通知；
2. request/response RPC：把 BLE 收到的查询 action 转发给 ws_handler 并等响应。

主应用未启动时静默重连（指数退避），BLE 配网功能不受影响。
"""
from __future__ import annotations

import asyncio
import itertools
import json
import logging
from typing import Awaitable, Callable, Dict, Optional

logger = logging.getLogger(__name__)

LOCAL_WS_URL = "ws://127.0.0.1:8000/ws/status"

# 广播事件回调：async fn(message_dict)
OnBroadcast = Callable[[dict], Awaitable[None]]

RECONNECT_MIN = 1.0
RECONNECT_MAX = 30.0
REQUEST_TIMEOUT = 10.0


class DhlrBridge:
    def __init__(self, url: str = LOCAL_WS_URL, on_broadcast: Optional[OnBroadcast] = None) -> None:
        self._url = url
        self._on_broadcast = on_broadcast
        self._ws = None
        self._pending: Dict[str, asyncio.Future] = {}
        self._msg_id = itertools.count(1)
        self._task: Optional[asyncio.Task] = None

    @property
    def connected(self) -> bool:
        return self._ws is not None

    def start(self) -> None:
        """启动常驻重连循环（幂等）。"""
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(self._run())

    async def _run(self) -> None:
        import websockets

        backoff = RECONNECT_MIN
        while True:
            try:
                async with websockets.connect(self._url, max_size=8 * 1024 * 1024) as ws:
                    self._ws = ws
                    backoff = RECONNECT_MIN
                    logger.info("[bridge] 已连接主应用 WS: %s", self._url)
                    async for raw in ws:
                        await self._dispatch(raw)
            except asyncio.CancelledError:
                raise
            except Exception as e:  # noqa: BLE001
                logger.debug("[bridge] 连接断开/失败: %s", e)
            finally:
                self._ws = None
                self._fail_pending("bridge disconnected")
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, RECONNECT_MAX)

    async def _dispatch(self, raw: str) -> None:
        try:
            msg = json.loads(raw)
        except ValueError:
            return
        if not isinstance(msg, dict):
            return
        if msg.get("type") == "response":
            fut = self._pending.pop(str(msg.get("msg_id")), None)
            if fut is not None and not fut.done():
                fut.set_result(msg)
            return
        if self._on_broadcast is not None:
            try:
                await self._on_broadcast(msg)
            except Exception as e:  # noqa: BLE001
                logger.warning("[bridge] 广播回调异常: %s", e)

    def _fail_pending(self, reason: str) -> None:
        for fut in self._pending.values():
            if not fut.done():
                fut.set_result({"type": "response", "success": False, "error": reason})
        self._pending.clear()

    async def request(self, action: str, params: Optional[dict] = None,
                      timeout: float = REQUEST_TIMEOUT) -> dict:
        """转发一条 action 给主应用，返回 {success, data|error}。"""
        ws = self._ws
        if ws is None:
            return {"success": False, "error": "dhlr app unreachable"}
        msg_id = f"ble-{next(self._msg_id)}"
        fut: asyncio.Future = asyncio.get_running_loop().create_future()
        self._pending[msg_id] = fut
        try:
            await ws.send(json.dumps({
                "type": "request", "msg_id": msg_id,
                "action": action, "params": params or {},
            }))
            resp = await asyncio.wait_for(fut, timeout)
            return {
                "success": bool(resp.get("success")),
                "data": resp.get("data"),
                "error": resp.get("error"),
            }
        except asyncio.TimeoutError:
            return {"success": False, "error": "timeout"}
        except Exception as e:  # noqa: BLE001
            return {"success": False, "error": str(e)}
        finally:
            self._pending.pop(msg_id, None)
