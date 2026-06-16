"""ProvisioningService 状态机单测（注入 fakes，不触碰 nmcli/http/BLE）。"""
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
