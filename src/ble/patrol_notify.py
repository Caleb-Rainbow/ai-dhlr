"""巡检广播 → BLE 通知整理（纯函数，无 bless/BLE 依赖，可单测）。

主应用 patrol_event 广播经 DhlrBridge 到达 on_broadcast（__main__.py），由此处整理成
event=patrol 的 STATUS 通知对象交给 gatt.notify。把这段纯逻辑从 __main__（拖入 bless）
剥离出来，使其可在开发机单测（照 protocol.py / service.py 的可测性惯例）。
"""
from __future__ import annotations

import json

from .protocol import MAX_FRAME_PAYLOAD

# 巡检结果裁剪兜底条数：设备 PatrolState.to_dict 已限 results[-20:]，通常 ~3KB < 8192；
# 仅当单帧仍超上限（畸形长 message 等）时启用，裁到最近若干条。
PATROL_RESULTS_FALLBACK = 8


def _patrol_notify(data: dict) -> dict:
    """把主应用 patrol_event 的 data（PatrolState）整理成 BLE STATUS 通知对象（event=patrol）。

    整态替换（非增量）——App 收到即整体覆盖巡检卡。字段与设备 ``PatrolState.to_dict`` 对齐：
    is_active / current_step / progress / message / results（每条 zone_id/zone_name/step/status/message/timestamp）。
    """
    return {
        "event": "patrol",
        "is_active": bool(data.get("is_active")),
        "current_step": data.get("current_step"),
        "progress": data.get("progress"),
        "message": data.get("message"),
        "results": list(data.get("results") or []),
    }


def _ensure_patrol_frame_safe(notify_obj: dict) -> dict:
    """单帧 payload 超 [MAX_FRAME_PAYLOAD] 时裁剪 results 到最近 [PATROL_RESULTS_FALLBACK] 条（置
    results_truncated=True）。否则 encode_frame 对超大 payload 抛异常，notify 静默丢帧——App 巡检卡停摆。
    正常巡检态 ~3KB 不触发；返回新 dict，不改原对象。
    """
    if len(json.dumps(notify_obj, ensure_ascii=False).encode("utf-8")) <= MAX_FRAME_PAYLOAD:
        return notify_obj
    trimmed = dict(notify_obj)
    trimmed["results"] = list(notify_obj.get("results") or [])[-PATROL_RESULTS_FALLBACK:]
    trimmed["results_truncated"] = True
    return trimmed
