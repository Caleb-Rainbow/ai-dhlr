"""静态 IP 协议、参数校验、真实 nmcli 命令形状与失败恢复。"""
import subprocess

import pytest

from src.ble.ipv4_config import validate_ipv4, nmcli_ipv4_args
from src.ble.network_applier import NetworkApplier, ETHERNET_CONN, PROVISIONED_CONN, _default_runner
from src.ble.service import ProvisioningService

STATIC = {"mode": "static", "address": "192.168.2.158", "netmask": "255.255.255.0",
          "gateway": "192.168.2.1", "dns": ["192.168.2.1", "8.8.8.8"]}


@pytest.mark.parametrize("key,value", [
    ("address", "192.168.2.256"), ("address", "192.168.2.0"),
    ("address", "192.168.2.255"), ("address", "127.0.0.1"), ("address", "224.0.0.1"),
    ("netmask", "255.0.255.0"), ("netmask", "0.0.0.0"), ("netmask", "0.0.0.255"),
    ("netmask", "24"), ("gateway", "192.168.3.1"), ("gateway", "192.168.2.158"),
    ("gateway", "192.168.2.255"), ("dns", "8.8.8.8"), ("dns", ["bad"]),
    ("dns", ["8.8.8.8"] * 5), ("mode", "invalid"),
])
def test_invalid_ipv4_rejected(key, value):
    with pytest.raises(ValueError):
        validate_ipv4({**STATIC, key: value})


def test_lan_only_and_dhcp_clear_static():
    assert validate_ipv4({**STATIC, "gateway": "", "dns": []})["gateway"] == ""
    args = nmcli_ipv4_args(validate_ipv4({"mode": "dhcp", "address": "bad"}))
    props = dict(zip(args[::2], args[1::2]))
    assert props == {"ipv4.method": "auto", "ipv4.addresses": "", "ipv4.gateway": "",
                     "ipv4.dns": "", "ipv4.ignore-auto-dns": "no", "ipv4.may-fail": "no"}


def test_wifi_static_activates_manual_profile():
    calls = []
    def runner(cmd):
        calls.append(cmd)
        if "wifi" in cmd and "list" in cmd:
            return 0, "Office:WPA2"
        if "IP4.ADDRESS" in cmd:
            return 0, "192.168.2.158/24"
        return 0, ""
    result = NetworkApplier(runner=runner).connect("Office", "password", STATIC)
    assert result["ok"] and result["ip"] == STATIC["address"]
    add = next(cmd for cmd in calls if "add" in cmd)
    assert add[add.index("ipv4.method") + 1] == "manual"
    assert add[add.index("ipv4.addresses") + 1] == "192.168.2.158/24"
    assert add[add.index("ipv4.dns") + 1] == "192.168.2.1,8.8.8.8"
    assert ["nmcli", "connection", "up", PROVISIONED_CONN] in calls


@pytest.mark.parametrize("kind", ["wifi", "ethernet"])
def test_invalid_input_performs_no_network_commands(kind):
    calls = []
    applier = NetworkApplier(runner=lambda cmd: (calls.append(cmd) or (0, "")))
    bad = {**STATIC, "netmask": "255.0.255.0"}
    result = applier.connect("Office", "password", bad) if kind == "wifi" else applier.connect_ethernet(bad)
    assert not result["ok"] and calls == []


@pytest.mark.parametrize("fail", [False, True, "exception"])
def test_ethernet_uses_uuid_keeps_other_profiles_and_recovers(fail):
    calls = []
    old_uuid = "00000000-0000-0000-0000-000000000001"
    stale_uuid = "00000000-0000-0000-0000-000000000002"
    def runner(cmd):
        calls.append(cmd)
        if "GENERAL.CON-UUID" in cmd:
            return 0, old_uuid
        if "up" in cmd and cmd[-1] != old_uuid:
            if fail == "exception":
                raise TimeoutError("nmcli timeout")
            return (1, "link down") if fail else (0, "activated")
        if "IP4.ADDRESS" in cmd:
            return 0, "192.168.2.158/24"
        if "NAME,UUID" in cmd:
            return 0, f"Original:{old_uuid}\n{ETHERNET_CONN}:{stale_uuid}"
        return 0, ""
    applier = NetworkApplier(runner=runner)
    if fail == "exception":
        with pytest.raises(TimeoutError):
            applier.connect_ethernet(STATIC)
    else:
        assert applier.connect_ethernet(STATIC)["ok"] is (not fail)
    add = next(cmd for cmd in calls if "add" in cmd)
    candidate = add[add.index("connection.uuid") + 1]
    assert "eth0" in add and "999" in add
    assert not any("wlan0" in cmd or "Hotspot" in cmd for cmd in calls)
    if fail:
        assert ["nmcli", "connection", "delete", "uuid", candidate] in calls
        assert ["nmcli", "connection", "up", "uuid", old_uuid] in calls
    else:
        assert ["nmcli", "connection", "delete", "uuid", stale_uuid] in calls
        assert not any("delete" in cmd and old_uuid in cmd for cmd in calls)


def test_get_config_reads_persistent_static_profile_without_wifi_password():
    def runner(cmd):
        if cmd[-1] == PROVISIONED_CONN:
            return 1, "not found"
        values = {"ipv4.method": "manual", "ipv4.addresses": "192.168.2.158/24",
                  "ipv4.gateway": "192.168.2.1", "ipv4.dns": "192.168.2.1,8.8.8.8"}
        return 0, values.get(cmd[2], "")
    assert NetworkApplier(runner=runner).current_config() == {"ethernet": {"ipv4": STATIC}}


class FakeNetwork:
    def __init__(self):
        self.calls = []
    def connect(self, *args):
        self.calls.append(("wifi", args))
        return {"ok": True, "ip": STATIC["address"]}
    def connect_ethernet(self, ipv4):
        self.calls.append(("ethernet", ipv4))
        return {"ok": True, "ip": STATIC["address"]}
    def current_config(self):
        return {"ethernet": {"ipv4": STATIC}}


class FakeDhlr:
    def apply_config(self, *args):
        raise AssertionError("network-only config must not update remote/system")


@pytest.mark.parametrize("kind", ["wifi", "ethernet"])
async def test_ble_set_config_apply_routes_correct_interface(kind):
    events, network = [], FakeNetwork()
    async def notify(obj):
        events.append(obj)
    svc = ProvisioningService(network, FakeDhlr(), notify)
    part = {"ipv4": STATIC, **({"ssid": "Office", "password": "password"} if kind == "wifi" else {})}
    await svc.handle(1, {"cmd": "set_config", "config": {kind: part}})
    await svc.handle(2, {"cmd": "apply"})
    assert len(network.calls) == 1 and network.calls[0][0] == kind
    assert events[-1]["state"] == "CONNECTED" and events[-1]["ip"] == STATIC["address"]
    assert ("ssid" in events[-1]) is (kind == "wifi")


async def test_invalid_config_never_applies_stale_payload():
    events, network = [], FakeNetwork()
    async def notify(obj):
        events.append(obj)
    svc = ProvisioningService(network, FakeDhlr(), notify)
    await svc.handle(1, {"cmd": "set_config", "config": {"ethernet": {"ipv4": STATIC}}})
    await svc.handle(2, {"cmd": "set_config", "config": {"ethernet": {"ipv4": {**STATIC, "address": "bad"}}}})
    await svc.handle(3, {"cmd": "apply"})
    assert not network.calls and events[-1]["code"] == "bad_request"


async def test_get_config_advertises_capability_in_framed_event():
    events = []
    async def notify(obj):
        events.append(obj)
    svc = ProvisioningService(FakeNetwork(), FakeDhlr(), notify)
    await svc.handle(1, {"cmd": "get_config"})
    assert events[-1]["network_config_version"] == 1
    assert events[-1]["config"]["ethernet"]["ipv4"] == STATIC


def test_default_runner_timeout_returns_failure_without_exposing_password(monkeypatch):
    command = ["nmcli", "connection", "add", "wifi-sec.psk", "secret-password"]
    def timed_out(*args, **kwargs):
        raise subprocess.TimeoutExpired(args[0], kwargs["timeout"])
    monkeypatch.setattr(subprocess, "run", timed_out)
    rc, error = _default_runner(command)
    assert rc != 0 and "timed out" in error
    assert "secret-password" not in error


async def test_ethernet_subprocess_timeout_restores_connection_and_allows_retry(monkeypatch):
    events, calls = [], []
    previous = "00000000-0000-0000-0000-000000000001"
    fail_activation = True
    def run(cmd, **kwargs):
        calls.append(cmd)
        out = ""
        if "GENERAL.CON-UUID" in cmd:
            out = previous
        elif "up" in cmd and cmd[-1] != previous and fail_activation:
            raise subprocess.TimeoutExpired(cmd, kwargs["timeout"])
        elif "IP4.ADDRESS" in cmd:
            out = STATIC["address"] + "/24"
        return subprocess.CompletedProcess(cmd, 0, out, "")
    monkeypatch.setattr(subprocess, "run", run)
    async def notify(obj):
        events.append(obj)
    svc = ProvisioningService(NetworkApplier(), FakeDhlr(), notify)
    await svc.handle(1, {"cmd": "set_config", "config": {"ethernet": {"ipv4": STATIC}}})
    await svc.handle(2, {"cmd": "apply"})
    assert svc.state == "IDLE"
    assert events[-2]["code"] == "ethernet_connect_failed"
    candidate = next(cmd for cmd in calls if "add" in cmd)
    candidate = candidate[candidate.index("connection.uuid") + 1]
    assert ["nmcli", "connection", "delete", "uuid", candidate] in calls
    assert ["nmcli", "connection", "up", "uuid", previous] in calls
    await svc.handle(3, {"cmd": "scan_wifi"})
    assert events[-2]["event"] == "scan_results" and svc.state == "IDLE"
    fail_activation = False
    await svc.handle(4, {"cmd": "apply"})
    assert svc.state == "CONNECTED" and events[-1]["ip"] == STATIC["address"]


@pytest.mark.parametrize("kind", ["wifi", "ethernet"])
async def test_unexpected_apply_exception_resets_state_and_allows_cancel_and_scan(kind):
    events = []
    class FailedNetwork(FakeNetwork):
        def connect(self, *args):
            raise OSError("network unavailable")
        def connect_ethernet(self, ipv4):
            raise OSError("network unavailable")
        def scan(self):
            return []
    async def notify(obj):
        events.append(obj)
    svc = ProvisioningService(FailedNetwork(), FakeDhlr(), notify)
    part = {"ipv4": STATIC, **({"ssid": "Office"} if kind == "wifi" else {})}
    await svc.handle(1, {"cmd": "set_config", "config": {kind: part}})
    await svc.handle(2, {"cmd": "apply"})
    assert events[-2]["code"] == "internal" and svc.state == "IDLE"
    await svc.handle(3, {"cmd": "cancel"})
    assert events[-1]["state"] == "IDLE" and svc._pending_config is None
    await svc.handle(4, {"cmd": "scan_wifi"})
    assert events[-2]["event"] == "scan_results" and events[-1]["state"] == "IDLE"
