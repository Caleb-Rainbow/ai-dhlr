"""区域查询协议字段回归测试。"""
from types import SimpleNamespace

import pytest

from src.api.ws_handler import WSHandler
from src.utils.config import config_manager


@pytest.fixture
def zone_query(monkeypatch):
    from src.serial_port.serial_manager import serial_manager
    from src.zone.state_machine import zone_manager

    runtime_zone = SimpleNamespace(
        id="zone_1",
        name="测试区域",
        camera_id="cam_1",
        camera_ids=["cam_1", "cam_2"],
        roi=[],
        enabled=True,
    )
    state_machine = SimpleNamespace(zone=runtime_zone)
    stored_zone = SimpleNamespace(
        id="zone_1",
        serial_index=2,
        fire_current_threshold=150,
        temp_sensor_address=7,
    )

    monkeypatch.setattr(zone_manager, "get_all_zones", lambda: [state_machine])
    monkeypatch.setattr(zone_manager, "get_zone", lambda zone_id: state_machine if zone_id == "zone_1" else None)
    monkeypatch.setattr(serial_manager, "get_all_currents", lambda: {"zone_1": 88})
    monkeypatch.setattr(config_manager.config, "zones", [stored_zone])
    return WSHandler()


async def test_get_zones_returns_multi_camera_and_temperature_contract(zone_query):
    result = await zone_query._get_zones({})

    assert result == [{
        "id": "zone_1",
        "name": "测试区域",
        "camera_id": "cam_1",
        "camera_ids": ["cam_1", "cam_2"],
        "roi": [],
        "enabled": True,
        "current_value": 88,
        "temp_sensor_address": 7,
        "temp_sensor_enabled": True,
        "serial_index": 2,
        "fire_current_threshold": 150,
    }]


async def test_get_zone_matches_get_zones_configuration_fields(zone_query):
    result = await zone_query._get_zone({"zone_id": "zone_1"})

    assert result["camera_ids"] == ["cam_1", "cam_2"]
    assert result["serial_index"] == 2
    assert result["fire_current_threshold"] == 150
    assert result["current_value"] == 88
    assert result["temp_sensor_address"] == 7
    assert result["temp_sensor_enabled"] is True
