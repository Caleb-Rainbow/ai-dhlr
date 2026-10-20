"""脚本启动后，API 新建灶台仍须触发实际系统的语音和切电回调。"""
import runpy
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

import src.main as main_module
import src.zone.state_machine as state_module
from src.api.ws_handler import WSHandler
from src.serial_port.serial_manager import serial_manager
from src.utils.config import AlarmConfig, ZoneConfig, config_manager
from src.zone.models import ZoneState
from src.zone.state_machine import zone_manager


@pytest.fixture
def isolated_manager(monkeypatch):
    monkeypatch.setattr(zone_manager, "_zones", {})
    monkeypatch.setattr(zone_manager, "_fire_states", {})
    monkeypatch.setattr(zone_manager, "_callbacks", {})
    return zone_manager


async def test_new_zone_inherits_script_entry_callbacks(isolated_manager, monkeypatch):
    # 与 systemd 的脚本入口具有相同模块隔离，避免真的调用 main()/初始化硬件。
    entry = runpy.run_path(str(Path(main_module.__file__)), run_name="audit_script_entry")
    system = entry["FireSafetySystem"]()
    system._logger = MagicMock()
    system._ensure_broadcast_manager_running = MagicMock()
    script_globals = system._on_warning.__func__.__globals__
    script_globals["_system"] = system
    monkeypatch.setattr(main_module, "_system", None)
    for name in ("sync_broadcast_state_change", "sync_broadcast_alarm_event",
                 "sync_upload_alarm_record"):
        monkeypatch.setitem(script_globals, name, MagicMock())

    cfg = SimpleNamespace(
        zones=[], system=SimpleNamespace(zone_mode="single"),
        alarm=AlarmConfig(warning_time=5, alarm_time=10, action_time=15),
    )
    monkeypatch.setattr(config_manager, "_config", cfg)
    monkeypatch.setattr(config_manager, "save", MagicMock())
    monkeypatch.setattr(main_module.camera_manager, "get_camera", MagicMock())
    monkeypatch.setattr(serial_manager, "register_zone", MagicMock())
    monkeypatch.setattr(serial_manager, "cutoff", MagicMock())
    monkeypatch.setattr(state_module, "event_logger", MagicMock())
    clock = SimpleNamespace(now=1000.0)
    monkeypatch.setattr(state_module, "time", SimpleNamespace(
        monotonic=lambda: clock.now, time=lambda: clock.now,
    ))
    callbacks = {
        name: getattr(system, "_" + name)
        for name in ("on_warning", "on_alarm", "on_cutoff", "on_state_change", "on_temp_alarm")
    }
    # 启动时零灶台也要保留回调，覆盖首次配网后的动态创建。
    isolated_manager.initialize_from_config([], **callbacks)
    assert main_module.get_zone_callbacks() == callbacks
    assert entry["get_zone_callbacks"]() == callbacks
    created = await WSHandler()._create_zone({"name": "回归测试", "camera_id": "cam_1"})
    sm = isolated_manager.get_zone(created["id"])
    for name, callback in callbacks.items():
        assert getattr(sm, "_" + name) == callback

    sm.update(False, True)
    for elapsed, expected, audio in (
        (6, ZoneState.WARNING, "warning"),
        (11, ZoneState.ALARM, "alarm"),
        (16, ZoneState.CUTOFF, "action"),
    ):
        clock.now = 1000 + elapsed
        assert sm.update(False, True) == expected
        assert system._broadcast_stop_flags[created["id"]]["audio_type"] == audio
    serial_manager.cutoff.assert_called_once_with(created["id"])


def test_explicit_zone_callback_overrides_default(isolated_manager):
    default = MagicMock()
    override = MagicMock()
    isolated_manager.initialize_from_config([], on_alarm=default, on_cutoff=default)
    sm = isolated_manager.add_zone(
        ZoneConfig(id="zone_1", name="测试", camera_id="cam_1", roi=[]),
        on_alarm=override,
    )
    assert sm._on_alarm is override
    assert sm._on_cutoff is default
