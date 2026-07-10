"""巡检广播转 BLE 通知的纯函数单测（_patrol_notify / _ensure_patrol_frame_safe）。

验证 on_broadcast 把主应用 patrol_event.data 整理成 event=patrol 通知对象，
以及单帧超 8192 时的 results 裁剪兜底（防 encode_frame 抛异常致 notify 静默丢帧）。
"""
from src.ble.patrol_notify import (
    PATROL_RESULTS_FALLBACK,
    _ensure_patrol_frame_safe,
    _patrol_notify,
)
from src.ble.protocol import MAX_FRAME_PAYLOAD


def _sample_result(zone="1", status="warning"):
    return {
        "zone_id": zone,
        "zone_name": f"{zone}号灶台",
        "step": "离人检测",
        "status": status,
        "message": f"{zone}号灶台检测到有人，请人员离开",
        "timestamp": 1752100000000,
    }


def test_patrol_notify_maps_fields():
    data = {
        "is_active": True,
        "current_step": "self_check_person",
        "progress": 35,
        "message": "检测到1号灶台有人",
        "results": [_sample_result()],
    }
    obj = _patrol_notify(data)
    assert obj["event"] == "patrol"
    assert obj["is_active"] is True
    assert obj["current_step"] == "self_check_person"
    assert obj["progress"] == 35
    assert obj["message"] == "检测到1号灶台有人"
    assert obj["results"] == [_sample_result()]


def test_patrol_notify_coerces_is_active_and_defaults():
    # is_active 真值非 bool → 强制 bool；缺字段 → 默认空态
    obj = _patrol_notify({"is_active": 1, "results": None})
    assert obj["is_active"] is True
    assert obj["results"] == []
    assert obj["current_step"] is None
    assert obj["progress"] is None


def test_patrol_notify_empty_data():
    obj = _patrol_notify({})
    assert obj["is_active"] is False
    assert obj["results"] == []


def test_ensure_safe_under_limit_unchanged():
    obj = _patrol_notify({"is_active": True, "progress": 50, "results": [_sample_result()]})
    assert _ensure_patrol_frame_safe(obj) is obj  # 未超限：原对象直返


def test_ensure_safe_trims_when_over_limit():
    # 构造超 8192 单帧的 results（每条 message ~120B × 200 → ~24KB）
    big = _patrol_notify({
        "is_active": True,
        "progress": 80,
        "message": "x" * 200,
        "results": [_sample_result(zone=str(i), status="success") for i in range(200)],
    })
    assert len(big["results"]) == 200
    trimmed = _ensure_patrol_frame_safe(big)
    assert trimmed is not big  # 返回新对象
    assert len(trimmed["results"]) == PATROL_RESULTS_FALLBACK
    # 保留最近若干条（倒序裁剪：200..193）
    assert trimmed["results"][-1]["zone_id"] == "199"
    assert trimmed["results"][0]["zone_id"] == str(200 - PATROL_RESULTS_FALLBACK)
    assert trimmed["results_truncated"] is True
    # 裁剪后须落入帧上限
    import json
    assert len(json.dumps(trimmed, ensure_ascii=False).encode("utf-8")) <= MAX_FRAME_PAYLOAD
    # 原对象不被改动
    assert "results_truncated" not in big
    assert len(big["results"]) == 200
