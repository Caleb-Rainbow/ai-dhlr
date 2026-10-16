import io
import json
from types import SimpleNamespace

import pytest

from deploy import upgrade_check as check


@pytest.fixture
def clock(monkeypatch):
    state = SimpleNamespace(now=0)
    monkeypatch.setattr(check.time, "monotonic", lambda: state.now)
    monkeypatch.setattr(check.time, "sleep", lambda seconds: setattr(state, "now", state.now + seconds))
    return state


def test_acceptance_requires_continuous_stable_processes(monkeypatch, clock):
    def signature(unit):
        # 主服务在第 4 秒发生重启；必须从此重新累计稳定窗口。
        return (10, 0) if clock.now < 4 or unit.endswith("ble.service") else (11, 1)

    monkeypatch.setattr(check, "service_signature", signature)
    monkeypatch.setattr(check, "check_api", lambda *_: None)
    check.wait_healthy("url", "version", timeout=20, stable_seconds=6)
    assert clock.now == 10


@pytest.mark.parametrize("failure", ["ble", "api", "crash_loop"])
def test_unhealthy_components_never_pass(monkeypatch, clock, failure):
    def signature(unit):
        if failure == "ble" and unit.endswith("ble.service"):
            raise RuntimeError("BLE inactive")
        return (100 + clock.now, clock.now) if failure == "crash_loop" else (100, 0)

    def api(*_):
        if failure == "api":
            raise RuntimeError("wrong firmware")

    monkeypatch.setattr(check, "service_signature", signature)
    monkeypatch.setattr(check, "check_api", api)
    with pytest.raises(RuntimeError, match="超时"):
        check.wait_healthy("url", "version", timeout=10, stable_seconds=4)


def test_api_failure_resets_stable_window(monkeypatch, clock):
    monkeypatch.setattr(check, "service_signature", lambda _: (10, 0))

    def api(*_):
        if clock.now == 4:
            raise OSError("temporary disconnect")

    monkeypatch.setattr(check, "check_api", api)
    check.wait_healthy("url", "version", timeout=20, stable_seconds=6)
    assert clock.now == 12


@pytest.mark.parametrize("payload,valid", [
    ({"magic": "DHLR_DEVICE", "firmware": "9.8.7"}, True),
    ({"magic": "DHLR_DEVICE", "firmware": "old"}, False),
    ({"firmware": "9.8.7"}, False),
    ([], False),
])
def test_api_requires_device_identity_and_expected_version(monkeypatch, payload, valid):
    def build_opener(proxy):
        assert proxy.proxies == {}
        return SimpleNamespace(open=lambda *args, **kwargs: io.BytesIO(json.dumps(payload).encode()))

    monkeypatch.setattr(check.urllib.request, "build_opener", build_opener)
    if valid:
        check.check_api("http://localhost", "9.8.7")
    else:
        with pytest.raises(RuntimeError):
            check.check_api("http://localhost", "9.8.7")


@pytest.mark.parametrize("state", ["inactive", "activating", "failed"])
def test_systemd_transition_is_not_running(monkeypatch, state):
    monkeypatch.setattr(check.subprocess, "run", lambda *args, **kwargs: SimpleNamespace(
        stdout=f"ActiveState={state}\nSubState=auto-restart\nMainPID=0\nNRestarts=2\n"))
    with pytest.raises(RuntimeError, match="未运行"):
        check.service_signature("ai-dhlr.service")


def test_settings_use_configured_address_and_do_not_modify_config(tmp_path):
    path = tmp_path / "config/config.yaml"
    path.parent.mkdir()
    original = 'api:\n  host: "::"\n  port: 8123\ninference:\n  engine: rknn\n  model_path: model.rknn\n'
    path.write_text(original, encoding="utf-8")
    url, engine, model = check.read_settings(tmp_path)
    assert url == "http://[::1]:8123/api/device/identify"
    assert engine == "rknn" and model == tmp_path / "model.rknn"
    assert path.read_text(encoding="utf-8") == original


def test_missing_rknn_dependency_fails_preflight(monkeypatch, tmp_path):
    monkeypatch.setattr(check, "read_settings", lambda _: ("url", "rknn", tmp_path / "model.rknn"))

    def import_module(name):
        if name == "rknnlite.api":
            raise ImportError("rknnlite is missing")

    monkeypatch.setattr(check.importlib, "import_module", import_module)
    with pytest.raises(ImportError, match="rknnlite"):
        check.preflight(tmp_path)


def test_missing_model_fails_preflight(monkeypatch, tmp_path):
    monkeypatch.setattr(check, "read_settings", lambda _: ("url", "rknn", tmp_path / "missing.rknn"))
    monkeypatch.setattr(check.importlib, "import_module", lambda _: None)
    with pytest.raises(ValueError, match="模型文件不存在"):
        check.preflight(tmp_path)
