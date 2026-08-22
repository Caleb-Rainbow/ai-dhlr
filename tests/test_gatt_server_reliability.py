"""BLE GATT 可靠性回归：通知背压、D-Bus 隔离与假活退出。"""

from __future__ import annotations

import asyncio
import importlib.util
import json
import sys
from enum import IntFlag
from types import ModuleType, SimpleNamespace
from unittest.mock import patch

from src.ble import protocol as P


def _load_gatt_server_module():
    """开发机未安装 Linux BLE 依赖时用最小桩加载被测模块。"""
    stubs = {}
    if importlib.util.find_spec("bless") is None:
        bless = ModuleType("bless")

        class _Properties(IntFlag):
            read = 1
            write = 2
            write_without_response = 4
            notify = 8

        class _Permissions(IntFlag):
            readable = 1
            writeable = 2

        bless.BlessGATTCharacteristic = object
        bless.BlessServer = object
        bless.GATTAttributePermissions = _Permissions
        bless.GATTCharacteristicProperties = _Properties
        stubs["bless"] = bless

    if importlib.util.find_spec("dbus_next") is None:
        dbus_next = ModuleType("dbus_next")
        dbus_next.__path__ = []
        aio = ModuleType("dbus_next.aio")
        constants = ModuleType("dbus_next.constants")
        message = ModuleType("dbus_next.message")

        class _MessageBus:
            pass

        class _BusType:
            SYSTEM = "system"

        class _MessageType:
            METHOD_CALL = 1
            METHOD_RETURN = 2

        class _Message:
            def __init__(self, **kwargs):
                self.__dict__.update(kwargs)

        aio.MessageBus = _MessageBus
        constants.BusType = _BusType
        constants.MessageType = _MessageType
        message.Message = _Message
        stubs.update(
            {
                "dbus_next": dbus_next,
                "dbus_next.aio": aio,
                "dbus_next.constants": constants,
                "dbus_next.message": message,
            }
        )

    with patch.dict(sys.modules, stubs):
        from src.ble import gatt_server

    return gatt_server


G = _load_gatt_server_module()


def _new_gatt():
    return G.GattServer(
        identity={"type": "dhlr", "model": "ai-dhlr", "fw": "1.0.6", "hwid": "a-b", "name": "AI动火离人"},
        name="AILRBJ-TEST",
    )


def test_notify_chunks_from_concurrent_messages_never_interleave(monkeypatch):
    gatt = _new_gatt()
    characteristic = SimpleNamespace(value=bytearray())
    updates = []

    class FakeServer:
        def get_characteristic(self, _uuid):
            return characteristic

        def update_value(self, _service_uuid, _characteristic_uuid):
            updates.append(bytes(characteristic.value))

    gatt._server = FakeServer()
    monkeypatch.setattr(G, "NOTIFY_CHUNK_INTERVAL_SEC", 0)
    monkeypatch.setattr(
        G.P,
        "encode_message",
        lambda _seq, obj, _mtu: [f"{obj['event']}1".encode(), f"{obj['event']}2".encode()],
    )

    async def scenario():
        await asyncio.gather(gatt.notify({"event": "A"}), gatt.notify({"event": "B"}))

    asyncio.run(scenario())
    assert updates == [b"A1", b"A2", b"B1", b"B2"]


def test_gatt_bus_disconnect_becomes_fatal():
    gatt = _new_gatt()

    class FailedBus:
        async def wait_for_disconnect(self):
            raise BlockingIOError(11, "Resource temporarily unavailable")

    gatt._server = SimpleNamespace(bus=FailedBus())

    async def scenario():
        await gatt._monitor_gatt_bus()
        try:
            await gatt.wait_until_failed()
        except RuntimeError as exc:
            return exc
        raise AssertionError("wait_until_failed 应抛出异常")

    error = asyncio.run(scenario())
    assert isinstance(error.__cause__, BlockingIOError)
    assert error.__cause__.errno == 11


def test_watchdog_query_uses_its_own_dbus_connection():
    gatt = _new_gatt()

    class MainGattBus:
        async def call(self, _message):
            raise AssertionError("看门狗不应调用 bless 的主 GATT D-Bus")

    class WatchdogBus:
        connected = True

        async def call(self, _message):
            return SimpleNamespace(
                message_type=G.MessageType.METHOD_RETURN,
                body=[
                    {
                        "/org/bluez/hci0/dev_AA_BB": {
                            "org.bluez.Device1": {"Connected": SimpleNamespace(value=True)}
                        }
                    }
                ],
            )

    gatt._server = SimpleNamespace(bus=MainGattBus())
    gatt._watchdog_bus = WatchdogBus()
    assert asyncio.run(gatt._any_central_connected()) is True


def test_device_info_uses_compact_json_for_single_read():
    network = {"connected": True, "ssid": "Factory-WiFi", "ip": "192.168.2.176"}
    raw = P.build_device_info(
        type="dhlr",
        model="ai-dhlr",
        fw="1.0.6",
        hwid="aa-bb-cc-dd-ee-ff",
        name="AI动火离人",
        network=network,
    )

    assert json.loads(raw) == {
        "type": "dhlr",
        "model": "ai-dhlr",
        "fw": "1.0.6",
        "hwid": "aa-bb-cc-dd-ee-ff",
        "name": "AI动火离人",
        "proto": 1,
        "network": network,
    }
    assert b'": "' not in raw
