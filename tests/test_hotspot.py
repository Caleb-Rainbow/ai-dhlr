"""utils/hotspot.py 单元测试。

sudo/systemctl/nmcli 调用经注入的 fake runner 替换，覆盖：
- 命令构造（含 sudo -S 前缀）
- is-enabled 输出解析（enabled/disabled/not-found/static/masked）
- get_state / enable / disable / apply_autostart
- disable 立即关掉 wlan0 上活动 AP 连接
- reconcile_with_config：一致时不操作、不一致时对齐、非 Linux 跳过
"""
import sys

import pytest

from src.utils import hotspot
from src.utils.hotspot import (
    HOTSPOT_UNIT,
    HotspotAutostart,
    disable_cmd,
    enable_cmd,
    is_enabled_cmd,
    parse_is_enabled,
)


class FakeRunner:
    def __init__(self, dispatch):
        self.dispatch = dispatch
        self.calls = []

    def __call__(self, cmd):
        self.calls.append(cmd)
        return self.dispatch(cmd)


# --------------------------------------------------------------------------- #
# 命令构造
# --------------------------------------------------------------------------- #
def test_commands_have_sudo_prefix():
    assert is_enabled_cmd() == ["sudo", "-S", "systemctl", "is-enabled", HOTSPOT_UNIT]
    assert enable_cmd() == ["sudo", "-S", "systemctl", "enable", HOTSPOT_UNIT]
    assert disable_cmd() == ["sudo", "-S", "systemctl", "disable", "--now", HOTSPOT_UNIT]


# --------------------------------------------------------------------------- #
# parse_is_enabled
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("out, rc, expected", [
    ("enabled\n", 0, "enabled"),
    ("disabled\n", 1, "disabled"),   # is-enabled 对 disabled 返回 rc=1
    ("static\n", 0, "enabled"),      # static 会被拉起，视为启用
    ("masked\n", 0, "disabled"),     # masked 永久关闭
    ("Failed to get unit file state... No such file or directory.\n", 1, "not-found"),
])
def test_parse_is_enabled(out, rc, expected):
    assert parse_is_enabled(out, rc) == expected


# --------------------------------------------------------------------------- #
# get_state
# --------------------------------------------------------------------------- #
def test_get_state_non_linux_unsupported(monkeypatch):
    # 强制非 Linux 平台 → supported=False，且不调用 runner（任意主机都确定）
    monkeypatch.setattr(sys, "platform", "win32")
    fake = FakeRunner(lambda cmd: (0, ""))
    ha = HotspotAutostart(runner=fake)
    assert ha.get_state() == {"supported": False, "enabled": False, "active": False}
    assert fake.calls == []


def test_get_state_enabled(monkeypatch):
    monkeypatch.setattr(sys, "platform", "linux")
    def dispatch(cmd):
        if "is-enabled" in cmd:
            return 0, "enabled\n"
        return 0, ""
    ha = HotspotAutostart(runner=FakeRunner(dispatch))
    assert ha.get_state() == {"supported": True, "enabled": True, "active": False}


def test_get_state_unit_missing(monkeypatch):
    monkeypatch.setattr(sys, "platform", "linux")
    def dispatch(cmd):
        return 1, "Failed to get unit file state: No such file or directory.\n"
    ha = HotspotAutostart(runner=FakeRunner(dispatch))
    assert ha.get_state()["supported"] is False


def test_get_state_active_hotspot(monkeypatch):
    monkeypatch.setattr(sys, "platform", "linux")
    def dispatch(cmd):
        joined = " ".join(cmd)
        if "is-enabled" in cmd:
            return 0, "enabled\n"
        if "NAME,DEVICE" in joined and "--active" in cmd:
            return 0, "Hotspot-3:wlan0\n"
        if "802-11-wireless.mode" in joined:
            return 0, "ap\n"
        return 0, ""
    ha = HotspotAutostart(runner=FakeRunner(dispatch))
    assert ha.get_state()["active"] is True


def test_active_ap_none_when_sta(monkeypatch):
    monkeypatch.setattr(sys, "platform", "linux")
    def dispatch(cmd):
        joined = " ".join(cmd)
        if "NAME,DEVICE" in joined and "--active" in cmd:
            return 0, "provisioned-wifi:wlan0\n"
        if "802-11-wireless.mode" in joined:
            return 0, "infrastructure\n"
        return 0, ""
    ha = HotspotAutostart(runner=FakeRunner(dispatch))
    assert ha.active_ap_connection() is None


# --------------------------------------------------------------------------- #
# enable / disable / apply_autostart
# --------------------------------------------------------------------------- #
def test_enable_ok(monkeypatch):
    monkeypatch.setattr(sys, "platform", "linux")
    ha = HotspotAutostart(runner=FakeRunner(lambda cmd: (0, "")))
    res = ha.enable()
    assert res["ok"] is True and res["enabled"] is True and res["supported"] is True


def test_enable_unit_missing(monkeypatch):
    monkeypatch.setattr(sys, "platform", "linux")
    def dispatch(cmd):
        return 1, "Failed to enable unit: Unit hotspot-startup.service does not exist.\n"
    ha = HotspotAutostart(runner=FakeRunner(dispatch))
    res = ha.enable()
    assert res["supported"] is False and res["ok"] is False


def test_disable_stops_active_ap(monkeypatch):
    monkeypatch.setattr(sys, "platform", "linux")
    def dispatch(cmd):
        joined = " ".join(cmd)
        if "disable" in cmd and "systemctl" in cmd:
            return 0, "Removed symlink.\n"
        if "NAME,DEVICE" in joined and "--active" in cmd:
            return 0, "Hotspot-3:wlan0\n"
        if "802-11-wireless.mode" in joined:
            return 0, "ap\n"
        if "down" in cmd:
            return 0, ""
        return 0, ""
    fake = FakeRunner(dispatch)
    ha = HotspotAutostart(runner=fake)
    res = ha.disable()
    assert res["ok"] is True and res["enabled"] is False
    assert res["stopped_hotspot"] is True
    joined = [" ".join(c) for c in fake.calls]
    assert any("systemctl disable --now" in j for j in joined)
    assert any("nmcli connection down Hotspot-3" in j for j in joined)


def test_disable_no_active_ap(monkeypatch):
    monkeypatch.setattr(sys, "platform", "linux")
    def dispatch(cmd):
        joined = " ".join(cmd)
        if "disable" in cmd and "systemctl" in cmd:
            return 0, "Removed\n"
        if "NAME,DEVICE" in joined and "--active" in cmd:
            return 0, "provisioned-wifi:wlan0\n"  # STA，非 AP
        if "802-11-wireless.mode" in joined:
            return 0, "infrastructure\n"
        return 0, ""
    ha = HotspotAutostart(runner=FakeRunner(dispatch))
    res = ha.disable()
    assert res["stopped_hotspot"] is False


def test_apply_autostart_routes(monkeypatch):
    monkeypatch.setattr(sys, "platform", "linux")
    ha = HotspotAutostart(runner=FakeRunner(lambda cmd: (0, "")))
    assert ha.apply_autostart(True)["enabled"] is True
    assert ha.apply_autostart(False)["enabled"] is False


# --------------------------------------------------------------------------- #
# reconcile_with_config
# --------------------------------------------------------------------------- #
def test_reconcile_skips_non_linux(monkeypatch):
    monkeypatch.setattr(sys, "platform", "win32")
    fake = FakeRunner(lambda cmd: (0, ""))
    hotspot.reconcile_with_config(True, runner=fake)
    assert fake.calls == []  # 非 Linux 直接跳过，不调 runner


def test_reconcile_noop_when_matches(monkeypatch):
    monkeypatch.setattr(sys, "platform", "linux")
    def dispatch(cmd):
        if "is-enabled" in cmd:
            return 0, "enabled\n"
        return 0, ""
    fake = FakeRunner(dispatch)
    hotspot.reconcile_with_config(True, runner=fake)
    joined = [" ".join(c) for c in fake.calls]
    assert not any("systemctl enable" in j for j in joined)
    assert not any("systemctl disable" in j for j in joined)


def test_reconcile_applies_when_differs(monkeypatch):
    monkeypatch.setattr(sys, "platform", "linux")
    def dispatch(cmd):
        if "is-enabled" in cmd:
            return 0, "enabled\n"   # 实际 enabled
        if "disable" in cmd:
            return 0, "Removed\n"
        return 0, ""
    fake = FakeRunner(dispatch)
    hotspot.reconcile_with_config(False, runner=fake)  # 配置要求关闭 → 触发 disable
    joined = [" ".join(c) for c in fake.calls]
    assert any("systemctl disable --now" in j for j in joined)
