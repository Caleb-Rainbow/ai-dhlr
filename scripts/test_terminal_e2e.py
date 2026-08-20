"""设备端终端 WS 链路验证：start → input → output → stop → exit"""
import asyncio
import base64
import json
import sys
import time

import aiohttp

URL = f"ws://{sys.argv[1] if len(sys.argv) > 1 else '192.168.2.176'}:8000/ws/status"


async def main():
    async with aiohttp.ClientSession() as s:
        async with s.ws_connect(URL, max_msg_size=4 * 1024 * 1024) as ws:
            # 1. 创建会话
            await ws.send_json({"type": "request", "msg_id": "m1",
                                "action": "terminal_start",
                                "params": {"shell": "bash", "cols": 100, "rows": 30}})
            session_id = None
            out = b""
            deadline = time.time() + 12
            while time.time() < deadline:
                msg = await asyncio.wait_for(ws.receive(), timeout=6)
                if msg.type != aiohttp.WSMsgType.TEXT:
                    print("WS closed:", msg.type, msg.data)
                    break
                data = json.loads(msg.data)
                t = data.get("type")
                if t == "response" and data.get("msg_id") == "m1":
                    print("terminal_start:", json.dumps(data, ensure_ascii=False)[:300])
                    if data.get("success"):
                        session_id = data["data"]["session_id"]
                        await ws.send_json({"type": "request", "msg_id": "m2",
                                            "action": "terminal_input",
                                            "params": {"session_id": session_id,
                                                       "data": "echo DHLR_E2E_OK\n"}})
                elif t == "terminal_output":
                    out += base64.b64decode(data["data"]["chunk_b64"])
                    if b"DHLR_E2E_OK" in out:
                        print("terminal_output: E2E 输出已收到 ✓")
                        await ws.send_json({"type": "request", "msg_id": "m3",
                                            "action": "terminal_stop",
                                            "params": {"session_id": session_id}})
                elif t == "terminal_exit":
                    print("terminal_exit:", json.dumps(data["data"], ensure_ascii=False))
                    print("=== 后端链路正常，输出前 200 字节: ===")
                    print(out[:200])
                    return
            print("超时未收到 terminal_exit；累计输出字节数:", len(out))


asyncio.run(main())
