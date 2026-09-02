"""network_applier / dhlr_client 单元测试。

nmcli/http 调用经注入的 fake runner/poster 替换，覆盖：
- 扫描解析（去重/排序/隐藏跳过/安全类型映射）
- 切网成功路径与失败→回退热点路径（命令构造正确）
- dhlr 配置下发成功/失败/空跳过
"""
import json

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
            return 0, "Hotspot-80:wlan0\n"
        if "802-11-wireless.mode" in cmd:
            return 0, "ap\n"
        if "wifi" in cmd and "list" in cmd:
            return 0, "MyWiFi:WPA2\n"
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
    # 关热点 + 建 STA（带 psk）+ 激活 都被调用
    joined = [" ".join(c) for c in fake.calls]
    assert any("connection down Hotspot-80" in j for j in joined)
    assert any(
        "con-name provisioned-wifi" in j and "MyWiFi" in j and "wifi-sec.psk pass1234" in j
        for j in joined
    )


def test_connect_failure_falls_back_to_hotspot():
    def dispatch(cmd):
        if "show" in cmd and "--active" in cmd:
            return 0, "Hotspot-80:wlan0\n"
        if "802-11-wireless.mode" in cmd:
            return 0, "ap\n"
        if "wifi" in cmd and "list" in cmd:
            return 0, "BadWiFi:WPA2\n"
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


def test_connect_deletes_duplicate_profiles_before_add():
    """nmcli add 不查重名；up <name> 命中同名第一个。切网前必须清光同名旧 profile。"""
    duplicates = [
        "provisioned-wifi:11111111-1111-1111-1111-111111111111",
        "provisioned-wifi:22222222-2222-2222-2222-222222222222",
        "other-conn:99999999-9999-9999-9999-999999999999",
    ]

    def dispatch(cmd):
        if "NAME,UUID" in cmd:
            return 0, "\n".join(duplicates) + "\n"
        if "delete" in cmd:
            return 0, ""
        if "wifi" in cmd and "list" in cmd:
            return 0, "MyWiFi:WPA2\n"
        if "add" in cmd:
            return 0, "added"
        if "up" in cmd:
            return 0, "activated"
        if "IP4.ADDRESS" in cmd:
            return 0, "192.168.1.50/24\n"
        return 0, ""

    fake = FakeRunner(dispatch)
    ap = NetworkApplier(runner=fake)
    assert ap.connect("MyWiFi", "pass1234")["ok"] is True
    deletes = [c for c in fake.calls if "delete" in c]
    assert sorted(c[-1] for c in deletes) == [
        "11111111-1111-1111-1111-111111111111",
        "22222222-2222-2222-2222-222222222222",
    ]
    # 删除发生在新建之前
    first_delete = fake.calls.index(deletes[0])
    first_add = next(i for i, c in enumerate(fake.calls) if "add" in c)
    assert first_delete < first_add


def test_connect_empty_password_on_secured_fails_fast():
    """加密网络空密码必须立刻失败，不能建出无 psk 的坏 profile（真机踩坑）。"""
    added = []

    def dispatch(cmd):
        if "wifi" in cmd and "list" in cmd:
            return 0, "MyWiFi:WPA2\n"
        if "add" in cmd:
            added.append(cmd)
            return 0, "added"
        return 0, ""

    ap = NetworkApplier(runner=FakeRunner(dispatch))
    res = ap.connect("MyWiFi", "")
    assert res["ok"] is False
    assert "password" in res["error"]
    assert added == []  # 未创建任何 profile


def test_connect_open_network_omits_wifi_sec():
    """开放网络不写 wifi-sec.*，空密码合法。"""

    def dispatch(cmd):
        if "wifi" in cmd and "list" in cmd:
            return 0, "FreeWiFi:\n"
        if "add" in cmd:
            return 0, "added"
        if "up" in cmd:
            return 0, "activated"
        if "IP4.ADDRESS" in cmd:
            return 0, "10.0.0.2/24\n"
        return 0, ""

    fake = FakeRunner(dispatch)
    ap = NetworkApplier(runner=fake)
    res = ap.connect("FreeWiFi", "")
    assert res["ok"] is True
    add_cmd = next(c for c in fake.calls if "add" in c)
    assert "wifi-sec.key-mgmt" not in add_cmd and "wifi-sec.psk" not in add_cmd


def test_connect_setup_failure_surfaced():
    """add 与 modify 双双失败时必须把 nmcli 错误带回给 App，而不是继续 up 报误导性错误。"""

    def dispatch(cmd):
        if "wifi" in cmd and "list" in cmd:
            return 0, "MyWiFi:WPA2\n"
        if "add" in cmd:
            return 1, "Error: failed to add: boom"
        if "modify" in cmd:
            return 1, "Error: unknown connection"
        return 0, ""

    ap = NetworkApplier(runner=FakeRunner(dispatch))
    res = ap.connect("MyWiFi", "pass1234")
    assert res["ok"] is False
    assert "connection setup failed" in res["error"] and "boom" in res["error"]


def test_active_hotspot_detected():
    def dispatch(cmd):
        if "show" in cmd and "--active" in cmd:
            return 0, "Hotspot-80:wlan0\n"
        if "802-11-wireless.mode" in cmd:
            return 0, "ap\n"
        return 0, ""

    ap = NetworkApplier(runner=FakeRunner(dispatch))
    assert ap.active_hotspot_connection() == "Hotspot-80"


def test_active_hotspot_none_when_sta():
    def dispatch(cmd):
        if "show" in cmd and "--active" in cmd:
            return 0, "provisioned-wifi:wlan0\n"
        if "802-11-wireless.mode" in cmd:
            return 0, "infrastructure\n"
        return 0, ""

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


def test_default_poster_real_http():
    """默认 poster 走真实 HTTP 回环（标准库 urllib，零第三方依赖）。

    曾因默认实现 import httpx 而设备未装，配网报 No module named httpx；
    且旧测试全注入 fake poster，默认路径零覆盖。此测试直接调 _default_poster，
    若再引入未声明依赖会在此立即失败。
    """
    import threading
    from http.server import BaseHTTPRequestHandler, HTTPServer

    from src.ble.dhlr_client import _default_poster

    received = []

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            body = self.rfile.read(int(self.headers["Content-Length"]))
            payload = json.loads(body)
            received.append((self.headers.get("Content-Type"), payload))
            # 带 remote 的请求回 200，其余回 500，覆盖两条返回路径
            code = 200 if "remote" in payload else 500
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(b'{"ok": true}' if code == 200 else b'{"detail": "boom"}')

        def log_message(self, *args):  # 静默测试期访问日志
            pass

    srv = HTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        base = f"http://127.0.0.1:{srv.server_port}/internal/apply-provisioning"
        status, text = _default_poster(base, {"remote": {"server_url": "x"}})
        assert status == 200 and '"ok"' in text
        # 非 2xx：HTTPError 被转成 (code, body) 返回而非抛异常
        status2, text2 = _default_poster(base, {"system": {"name": "y"}})
        assert status2 == 500 and "boom" in text2
        # 发出的请求体是 JSON 且带正确 Content-Type
        assert received[0] == ("application/json", {"remote": {"server_url": "x"}})
    finally:
        srv.shutdown()
