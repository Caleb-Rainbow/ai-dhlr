"""network_applier / dhlr_client 单元测试。

nmcli/http 调用经注入的 fake runner/poster 替换，覆盖：
- 扫描解析（去重/排序/隐藏跳过/安全类型映射）
- 切网成功路径与失败→回退热点路径（命令构造正确）
- dhlr 配置下发成功/失败/空跳过
"""
import pytest

from src.ble.network_applier import (
    NetworkApplier,
    PROVISIONED_CONN,
    _extract_ip4_addr,
    map_security,
    parse_scan_lines,
)
from src.ble.dhlr_client import DhlrClient


# --------------------------------------------------------------------------- #
# map_security
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    "raw, expected",
    [
        ("WPA3", "wpa3"),
        ("WPA2", "wpa2"),
        ("WPA1 WPA2", "wpa2"),
        ("WPA1", "wpa"),
        ("WPA", "wpa"),
        ("", "open"),
        ("--", "open"),
        ("WEP", "wep"),
        ("FT/IEEE802.1X", "unknown"),
    ],
)
def test_map_security(raw, expected):
    assert map_security(raw) == expected


# --------------------------------------------------------------------------- #
# parse_scan_lines
# --------------------------------------------------------------------------- #
def test_parse_scan_lines_dedup_sort_hidden():
    lines = [
        "TP-LINK_XC:62:WPA1 WPA2",
        "xiaofang:34:WPA2",
        ":27:WPA1 WPA2",        # 隐藏网络，跳过
        "OPEN_GUEST:50:",
        "TP-LINK_XC:70:WPA2",    # 同名更强信号，覆盖
    ]
    nets = parse_scan_lines(lines)
    assert [n["ssid"] for n in nets] == ["TP-LINK_XC", "OPEN_GUEST", "xiaofang"]
    # 同名取最强（signal=70 → rssi=-58）
    assert nets[0]["ssid"] == "TP-LINK_XC" and nets[0]["rssi"] == -58
    assert nets[0]["security"] == "wpa2"
    assert nets[1]["security"] == "open"
    # 降序
    assert nets[0]["rssi"] >= nets[1]["rssi"] >= nets[2]["rssi"]


# --------------------------------------------------------------------------- #
# _extract_ip4_addr
# --------------------------------------------------------------------------- #
def test_extract_ip4_addr():
    assert _extract_ip4_addr("192.168.1.50/24\n") == "192.168.1.50"
    assert _extract_ip4_addr("--\n") is None
    assert _extract_ip4_addr("") is None
    assert _extract_ip4_addr("10.0.0.1/8 192.168.1.5/24") == "10.0.0.1"


# --------------------------------------------------------------------------- #
# NetworkApplier: 扫描（注入 runner）
# --------------------------------------------------------------------------- #
def test_scan_uses_runner_and_parses():
    nmcli_out = "AP-A:80:WPA2\nAP-B:40:\n"

    def runner(cmd):
        if "rescan" in cmd:
            return 0, ""
        return 0, nmcli_out

    ap = NetworkApplier(runner=runner)
    nets = ap.scan()
    assert [n["ssid"] for n in nets] == ["AP-A", "AP-B"]
    assert nets[1]["security"] == "open"


# --------------------------------------------------------------------------- #
# NetworkApplier: 切网成功
# --------------------------------------------------------------------------- #
class FakeRunner:
    def __init__(self, dispatch):
        self.dispatch = dispatch
        self.calls = []

    def __call__(self, cmd):
        self.calls.append(cmd)
        return self.dispatch(cmd)


def test_connect_success():
    def dispatch(cmd):
        if "show" in cmd and "--active" in cmd:
            return 0, "Hotspot-80:802-11-wireless:wlan0:ap\n"
        if "down" in cmd:
            return 0, ""
        if "add" in cmd:
            return 0, "added"
        if "up" in cmd and PROVISIONED_CONN in cmd:
            return 0, "activated"
        if "IP4.ADDRESS" in cmd:
            return 0, "192.168.1.50/24\n"
        return 0, ""

    fake = FakeRunner(dispatch)
    ap = NetworkApplier(runner=fake)
    res = ap.connect("MyWiFi", "pass1234")
    assert res["ok"] is True
    assert res["ip"] == "192.168.1.50"
    assert res["ssid"] == "MyWiFi"
    # 关热点 + 建 STA + 激活 都被调用
    joined = [" ".join(c) for c in fake.calls]
    assert any("connection down Hotspot-80" in j for j in joined)
    assert any("con-name provisioned-wifi" in j and "MyWiFi" in j for j in joined)


def test_connect_failure_falls_back_to_hotspot():
    def dispatch(cmd):
        if "show" in cmd and "--active" in cmd:
            return 0, "Hotspot-80:802-11-wireless:wlan0:ap\n"
        if "down" in cmd:
            return 0, ""
        if "add" in cmd:
            return 0, "added"
        if "up" in cmd and PROVISIONED_CONN in cmd:
            return 1, "Error: secrets were invalid"
        if "up" in cmd and "Hotspot-80" in cmd:
            return 0, "activated"
        return 0, ""

    fake = FakeRunner(dispatch)
    ap = NetworkApplier(runner=fake)
    res = ap.connect("BadWiFi", "wrong")
    assert res["ok"] is False
    assert "invalid" in res["error"]
    # 失败后回退原热点
    joined = [" ".join(c) for c in fake.calls]
    assert any("connection up Hotspot-80" in j for j in joined)


def test_active_hotspot_none_when_sta():
    def dispatch(cmd):
        # 当前是 STA，无热点
        return 0, "provisioned-wifi:802-11-wireless:wlan0:infrastructure\n"

    ap = NetworkApplier(runner=FakeRunner(dispatch))
    assert ap.active_hotspot_connection() is None


# --------------------------------------------------------------------------- #
# DhlrClient
# --------------------------------------------------------------------------- #
def test_dhlr_client_apply_ok():
    def poster(url, payload):
        assert url.endswith("/internal/apply-provisioning")
        assert payload == {"remote": {"server_url": "x"}, "system": {"name": "y"}}
        return 200, '{"ok": true, "remote_reconnect": false}'

    c = DhlrClient(poster=poster)
    assert c.apply_config(remote={"server_url": "x"}, system={"name": "y"})["ok"] is True


def test_dhlr_client_apply_fail():
    def poster(url, payload):
        return 403, '{"detail":"forbidden: loopback only"}'

    c = DhlrClient(poster=poster)
    res = c.apply_config(remote={"server_url": "x"})
    assert res["ok"] is False
    assert res["error"] == "http 403"


def test_dhlr_client_apply_skips_empty():
    called = {"n": 0}

    def poster(url, payload):
        called["n"] += 1
        return 200, "{}"

    c = DhlrClient(poster=poster)
    res = c.apply_config()
    assert res["ok"] is True and res["skipped"] is True
    assert called["n"] == 0  # 无内容不下发
