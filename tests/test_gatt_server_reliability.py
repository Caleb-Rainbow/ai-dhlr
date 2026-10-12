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


def test_framed_device_info_matches_read_characteristic_and_uses_cached_network(monkeypatch):
    gatt = _new_gatt()
    gatt._network = {'uplink': 'ethernet', 'ethernet': {'ip': '192.168.0.235'}}
    def forbidden_query():
        raise AssertionError('设备信息必须使用网络缓存')
    monkeypatch.setattr(G, 'current_network_status', forbidden_query)
    info = gatt.device_info()
    assert info == json.loads(gatt._read(SimpleNamespace(uuid=P.DEVICE_INFO_UUID)))
    assert info['model'] == 'ai-dhlr' and info['fw'] == '1.0.6'
    info['network']['ethernet']['ip'] = 'changed'
    assert gatt._network['ethernet']['ip'] == '192.168.0.235'


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


def test_ios_notifications_use_observed_185_mtu_and_roundtrip_without_truncation(monkeypatch):
    gatt = _new_gatt()
    gatt._observe_gatt_options({"device": SimpleNamespace(value="/org/bluez/hci0/dev_IPHONE"), "mtu": SimpleNamespace(value=185)})
    characteristic = SimpleNamespace(value=bytearray())
    delivered = []

    class FakeServer:
        def get_characteristic(self, _uuid):
            return characteristic

        def update_value(self, _service_uuid, _characteristic_uuid):
            delivered.append(bytes(characteristic.value)[:182])  # BlueZ 的真实截断边界。

    gatt._server = FakeServer()
    monkeypatch.setattr(G, "NOTIFY_CHUNK_INTERVAL_SEC", 0)
    event = {"event": "current_config", "id": 1, "config": {"remote": {"server_url": "https://example.test/" + "x" * 400}}}
    asyncio.run(gatt.notify(event))
    assert max(map(len, delivered)) == 182
    assert P.MessageDecoder().feed(b"".join(delivered)) == [(1, event)]
    old = [chunk[:182] for chunk in P.encode_message(1, event, mtu=247)]
    assert P.MessageDecoder().feed(b"".join(old)) == []


def test_notification_mtu_fallback_and_android_large_mtu():
    gatt = _new_gatt()
    assert gatt._notification_mtu() == 23
    for invalid in (None, 0, 22, 999, True, "185"):
        gatt._observe_gatt_options({"device": "phone", "mtu": invalid})
        assert gatt._notification_mtu() == 23
    gatt._observe_gatt_options({"device": "android", "mtu": 247})
    assert gatt._notification_mtu() == 247
    gatt._observe_gatt_options({"device": "iphone", "mtu": 185})
    assert gatt._notification_mtu() == 185
    gatt._active_peers.add("not-yet-observed")
    assert gatt._notification_mtu() == 23


def test_disconnected_peer_mtu_and_partial_request_are_cleared():
    gatt = _new_gatt()
    gatt._observe_gatt_options({"device": "/org/bluez/hci0/dev_OLD", "mtu": 185})
    gatt._decoder.feed(b"\xa5\x00\x01\x00\x30partial")
    characteristic = SimpleNamespace(gatt=SimpleNamespace(_dhlr_read_snapshots={"/org/bluez/hci0/dev_OLD": (0, b"old")}))
    gatt._server = SimpleNamespace(get_characteristic=lambda _: characteristic)

    class WatchdogBus:
        connected = True

        async def call(self, _message):
            return SimpleNamespace(message_type=G.MessageType.METHOD_RETURN, body=[{}])

    gatt._watchdog_bus = WatchdogBus()
    assert asyncio.run(gatt._any_central_connected()) is False
    assert gatt._notification_mtu() == 23
    assert characteristic.gatt._dhlr_read_snapshots == {}
    assert gatt._decoder.feed(b"".join(P.encode_message(2, {"cmd": "get_config"}))) == [(2, {"cmd": "get_config"})]


def test_stale_connection_query_does_not_clear_new_handshake_mtu_or_partial_frame():
    gatt = _new_gatt()
    frame = b"".join(P.encode_message(7, {"cmd": "get_config"}))

    class WatchdogBus:
        connected = True

        async def call(self, _message):
            await asyncio.sleep(0)
            gatt._observe_gatt_options({"device": "/org/bluez/hci0/dev_NEW", "mtu": 185})
            gatt._decoder.feed(frame[:20])
            return SimpleNamespace(message_type=G.MessageType.METHOD_RETURN, body=[{}])

    gatt._watchdog_bus = WatchdogBus()
    assert asyncio.run(gatt._any_central_connected()) is True
    assert gatt._notification_mtu() == 185
    assert gatt._decoder.feed(frame[20:]) == [(7, {"cmd": "get_config"})]
