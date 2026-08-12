"""ProvisioningService 状态机单测（注入 fakes，不触碰 nmcli/http/BLE）。"""
import base64 as _b64

import pytest

from src.ble.service import (
    APPLYING,
    CONNECTED,
    CONNECTING,
    IDLE,
    SCANNING,
    ProvisioningService,
)


class FakeNetwork:
    def __init__(self, networks=None, connect_result=None):
        self.networks = networks or []
        self.connect_result = connect_result or {"ok": True, "ip": "192.168.1.10"}
        self.connect_calls = []

    def scan(self):
        return self.networks

    def connect(self, ssid, password):
        self.connect_calls.append((ssid, password))
        return self.connect_result


class FakeDhlr:
    def __init__(self, result=None):
        self.result = result or {"ok": True}
        self.calls = []

    def apply_config(self, remote=None, system=None):
        self.calls.append((remote, system))
        return self.result


class FakeNotify:
    def __init__(self):
        self.objs = []

    async def __call__(self, obj):
        self.objs.append(obj)


def _svc(net=None, dhlr=None, networks=None, connect_result=None, dhlr_result=None):
    if net is None:
        net = FakeNetwork(networks=networks, connect_result=connect_result)
    if dhlr is None:
        dhlr = FakeDhlr(result=dhlr_result)
    nf = FakeNotify()
    svc = ProvisioningService(net, dhlr, nf)
    return svc, nf, net, dhlr


# --------------------------------------------------------------------------- #
async def test_scan_flow():
    svc, nf, net, _ = _svc(networks=[{"ssid": "A", "rssi": -50, "security": "wpa2"}])
    await svc.handle(1, {"id": 100, "cmd": "scan_wifi"})
    events = [o["event"] for o in nf.objs]
    assert events == ["state_update", "scan_results", "state_update"]
    assert nf.objs[0]["state"] == SCANNING
    assert nf.objs[1]["networks"][0]["ssid"] == "A"
    assert nf.objs[2]["state"] == IDLE
    assert svc.state == IDLE


async def test_apply_success_full_flow():
    svc, nf, net, dhlr = _svc(connect_result={"ok": True, "ip": "192.168.1.20"})
    await svc.handle(1, {"id": 7, "cmd": "set_config", "config": {
        "wifi": {"ssid": "Home", "password": "pw"},
        "remote": {"server_url": "x"},
        "system": {"name": "n"},
    }})
    await svc.handle(2, {"id": 7, "cmd": "apply"})
    states = [o["state"] for o in nf.objs if o["event"] == "state_update"]
    assert states == ["IDLE", APPLYING, CONNECTING, CONNECTED]
    # remote/system 下发给了 dhlr
    assert dhlr.calls == [({"server_url": "x"}, {"name": "n"})]
    # wifi 凭据传给切网
    assert net.connect_calls == [("Home", "pw")]
    # CONNECTED 携带 ip
    connected = next(o for o in nf.objs if o.get("state") == CONNECTED)
    assert connected["ip"] == "192.168.1.20" and connected["ssid"] == "Home"
    assert svc.state == CONNECTED


async def test_apply_wifi_fail_reports_and_resets():
    svc, nf, net, _ = _svc(connect_result={"ok": False, "error": "secrets invalid"})
    await svc.handle(1, {"cmd": "set_config", "config": {"wifi": {"ssid": "X", "password": "y"}}})
    await svc.handle(2, {"cmd": "apply"})
    err = [o for o in nf.objs if o["event"] == "error"]
    assert err and err[0]["code"] == "wifi_connect_failed"
    assert "invalid" in err[0]["message"]
    assert svc.state == IDLE


async def test_apply_dhlr_fail_skips_wifi():
    svc, nf, net, _ = _svc(dhlr_result={"ok": False, "error": "boom"})
    await svc.handle(1, {"cmd": "set_config", "config": {
        "wifi": {"ssid": "X"}, "remote": {"server_url": "u"}}})
    await svc.handle(2, {"cmd": "apply"})
    err = [o for o in nf.objs if o["event"] == "error"]
    assert err[0]["code"] == "apply_failed"
    assert net.connect_calls == []  # dhlr 失败，不切网


async def test_apply_without_config_errors():
    svc, nf, _, _ = _svc()
    await svc.handle(1, {"cmd": "apply"})
    err = [o for o in nf.objs if o["event"] == "error"]
    assert err[0]["code"] == "bad_request"


async def test_unknown_cmd_errors():
    svc, nf, _, _ = _svc()
    await svc.handle(1, {"id": 9, "cmd": "frobnicate"})
    err = [o for o in nf.objs if o["event"] == "error"]
    assert err[0]["code"] == "bad_request" and err[0]["id"] == 9


async def test_set_config_bad_payload():
    svc, nf, _, _ = _svc()
    await svc.handle(1, {"cmd": "set_config", "config": "not-an-object"})
    err = [o for o in nf.objs if o["event"] == "error"]
    assert err[0]["code"] == "bad_request"


# ------------------------------ rpc 桥 ------------------------------ #
class FakeBridge:
    def __init__(self, result=None):
        self.result = result or {"success": True, "data": [{"id": "z1"}], "error": None}
        self.calls = []

    async def request(self, action, params=None, timeout=10.0):
        self.calls.append((action, params))
        return self.result


def _svc_with_bridge(bridge):
    nf = FakeNotify()
    svc = ProvisioningService(FakeNetwork(), FakeDhlr(), nf, bridge=bridge)
    return svc, nf


async def test_rpc_whitelisted_action_forwards_and_notifies():
    bridge = FakeBridge()
    svc, nf = _svc_with_bridge(bridge)
    await svc.handle(1, {"id": 5, "cmd": "rpc", "action": "get_status", "params": {}})
    assert bridge.calls == [("get_status", {})]
    r = next(o for o in nf.objs if o["event"] == "rpc_result")
    assert r["id"] == 5 and r["action"] == "get_status"
    assert r["success"] is True and r["data"] == [{"id": "z1"}]


async def test_rpc_rejects_non_whitelisted_action():
    bridge = FakeBridge()
    svc, nf = _svc_with_bridge(bridge)
    # toggle_fire（调试动作）不在白名单——勿用已白名单化的 trigger_update / patrol_force_cutoff
    await svc.handle(1, {"id": 6, "cmd": "rpc", "action": "toggle_fire"})
    assert bridge.calls == []
    err = [o for o in nf.objs if o["event"] == "error"]
    assert err[0]["code"] == "bad_request" and err[0]["id"] == 6


@pytest.mark.parametrize("action", [
    "get_cameras", "get_camera", "create_camera", "update_camera", "delete_camera",
    "get_usb_devices", "get_zone", "create_zone", "update_zone", "delete_zone",
])
async def test_rpc_whitelists_camera_zone_crud(action):
    """区域·摄像头管理覆盖层：camera/zone CRUD 已纳入白名单，应转发给桥而非被拒。"""
    bridge = FakeBridge()
    svc, nf = _svc_with_bridge(bridge)
    await svc.handle(1, {"id": 60, "cmd": "rpc", "action": action, "params": {"zone_id": "z"}})
    assert bridge.calls == [(action, {"zone_id": "z"})]
    assert not any(o["event"] == "error" for o in nf.objs)


@pytest.mark.parametrize("action", [
    "get_remote_config", "update_remote_config", "verify_remote_login",
    "get_lora_config", "set_lora_config",
    "get_usb_otg_mode", "set_usb_otg_mode",
    "trigger_update", "install_dependencies",
])
async def test_rpc_whitelists_system_config(action):
    """系统设置覆盖层：远程连接/LoRA/USB OTG/系统维护动作已纳入白名单，应转发给桥而非被拒。"""
    bridge = FakeBridge()
    svc, nf = _svc_with_bridge(bridge)
    await svc.handle(1, {"id": 70, "cmd": "rpc", "action": action, "params": {}})
    assert bridge.calls == [(action, {})]
    assert not any(o["event"] == "error" for o in nf.objs)


async def test_rpc_without_bridge_reports_unsupported():
    svc, nf, _, _ = _svc()
    await svc.handle(1, {"id": 7, "cmd": "rpc", "action": "get_status"})
    err = [o for o in nf.objs if o["event"] == "error"]
    assert err[0]["code"] == "unsupported"


async def test_rpc_bridge_failure_propagates_error():
    bridge = FakeBridge(result={"success": False, "data": None, "error": "timeout"})
    svc, nf = _svc_with_bridge(bridge)
    await svc.handle(1, {"id": 8, "cmd": "rpc", "action": "get_device"})
    r = next(o for o in nf.objs if o["event"] == "rpc_result")
    assert r["success"] is False and r["error"] == "timeout"


# ------------------------------ get_image / get_preview 多帧流式 ------------------------------ #
def _image_b64():
    return _b64.b64encode(b"\xff\xd8\xff\xd9" + b"x" * 10).decode()


async def test_get_image_streams_start_chunks_end():
    bridge = FakeBridge(result={"success": True, "data": {"image": "data:image/jpeg;base64," + _image_b64()}, "error": None})
    svc, nf = _svc_with_bridge(bridge)
    await svc.handle(1, {"id": 10, "cmd": "get_image", "filename": "zone_1_alarm.jpg"})
    assert bridge.calls == [("get_snapshot", {"filename": "zone_1_alarm.jpg"})]
    start = next(o for o in nf.objs if o["event"] == "image_start")
    chunks = [o for o in nf.objs if o["event"] == "image_chunk"]
    assert start["total_chunks"] == len(chunks)
    assert [c["index"] for c in chunks] == list(range(len(chunks)))
    assert any(o["event"] == "image_end" for o in nf.objs)
    rebuilt = _b64.b64decode("".join(c["data"] for c in chunks))
    assert rebuilt.startswith(b"\xff\xd8\xff")


async def test_get_image_empty_filename_sends_image_error():
    """早期校验失败须发 image_error（非 error）——否则 App awaitImage 挂 30s 超时。"""
    bridge = FakeBridge()
    svc, nf = _svc_with_bridge(bridge)
    await svc.handle(1, {"id": 11, "cmd": "get_image", "filename": ""})
    assert bridge.calls == []  # 校验在桥之前
    assert any(o["event"] == "image_error" and o["id"] == 11 for o in nf.objs)
    assert not any(o["event"] == "image_start" for o in nf.objs)


async def test_get_image_traversal_yields_no_image():
    """路径穿越被 basename 收敛为 'passwd' 转发——不会泄漏 /etc/passwd（无 image_start）。"""
    bridge = FakeBridge()  # 默认 result 的 data 是 list（无 image）→ image_error
    svc, nf = _svc_with_bridge(bridge)
    await svc.handle(1, {"id": 12, "cmd": "get_image", "filename": "../../etc/passwd"})
    assert bridge.calls == [("get_snapshot", {"filename": "passwd"})]  # 已 basename
    assert not any(o["event"] == "image_start" for o in nf.objs)


async def test_get_preview_missing_camera_id_sends_image_error():
    bridge = FakeBridge()
    svc, nf = _svc_with_bridge(bridge)
    await svc.handle(1, {"id": 13, "cmd": "get_preview"})  # 无 camera_id
    assert bridge.calls == []
    assert any(o["event"] == "image_error" and o["id"] == 13 for o in nf.objs)
    assert not any(o["event"] == "image_start" for o in nf.objs)


async def test_get_preview_bridge_failure_sends_image_error():
    bridge = FakeBridge(result={"success": False, "data": None, "error": "摄像头离线"})
    svc, nf = _svc_with_bridge(bridge)
    await svc.handle(1, {"id": 14, "cmd": "get_preview", "camera_id": "0"})
    err = [o for o in nf.objs if o["event"] == "image_error"]
    assert err and err[0]["id"] == 14 and "离线" in err[0]["error"]
