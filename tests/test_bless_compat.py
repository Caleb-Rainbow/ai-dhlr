"""真实 dbus-next 调度元数据回归；无 Linux 蓝牙硬件依赖。"""
import pytest

dbus_next = pytest.importorskip("dbus_next")
from dbus_next.service import ServiceInterface, method
from src.ble.bless_compat import install_bluez_compat


def _characteristic_type():
    class Characteristic(ServiceInterface):
        def __init__(self):
            self.payload = b'{"name":"original","network":"stable"}'
            self.reads = 0
            self.writes = []
            self.options = []
            self._dhlr_options_observer = self.options.append
            super().__init__("org.bluez.GattCharacteristic1")

        @method()
        def ReadValue(self, options: "a{sv}") -> "ay":
            self.reads += 1
            return self.payload  # 模拟 bless 0.3.0 忽略 offset。

        @method()
        def WriteValue(self, value: "ay", options: "a{sv}"):
            self.writes.append(bytes(value))

    return Characteristic


def _dispatch(characteristic, name, *args):
    method_info = next(m for m in ServiceInterface._get_methods(characteristic) if m.name == name)
    return method_info.fn(characteristic, *args)


def _options(offset=0, peer="/org/bluez/hci0/dev_AA_BB"):
    return {"offset": dbus_next.Variant("q", offset), "mtu": dbus_next.Variant("q", 185),
            "device": dbus_next.Variant("o", peer)}


def test_read_blob_slices_original_snapshot_and_preserves_dbus_signature():
    cls = _characteristic_type()
    assert install_bluez_compat(cls) is True
    char = cls()
    first = _dispatch(char, "ReadValue", _options())
    char.payload = b'{"network":"changed during long read"}'
    assert _dispatch(char, "ReadValue", _options(17)) == first[17:]
    assert _dispatch(char, "ReadValue", _options(len(first))) == b""
    assert char.reads == 1
    assert char.options[-1]["mtu"].value == 185
    interface = char.introspect()
    read = next(m for m in interface.methods if m.name == "ReadValue")
    assert read.in_args[0].signature == "a{sv}"
    assert read.out_args[0].signature == "ay"
    assert _dispatch(char, "ReadValue", _options()) == char.payload


def test_write_observes_options_before_unchanged_payload_and_is_idempotent():
    cls = _characteristic_type()
    install_bluez_compat(cls)
    read_fn = getattr(cls.ReadValue, "__DBUS_METHOD").fn
    install_bluez_compat(cls)
    assert getattr(cls.ReadValue, "__DBUS_METHOD").fn is read_fn
    char = cls()
    _dispatch(char, "WriteValue", b"123", _options())
    assert char.writes == [b"123"]
    assert char.options[0]["mtu"].value == 185


def test_invalid_read_offset_is_bluez_error_and_unbound_service_is_unchanged():
    cls = _characteristic_type()
    install_bluez_compat(cls)
    char = cls()
    _dispatch(char, "ReadValue", _options())
    with pytest.raises(dbus_next.DBusError) as error:
        _dispatch(char, "ReadValue", _options(len(char.payload) + 1))
    assert error.value.type == "org.bluez.Error.InvalidOffset"
    del char._dhlr_options_observer
    assert _dispatch(char, "ReadValue", _options(5)) == char.payload


def test_read_snapshots_are_isolated_by_peer():
    cls = _characteristic_type()
    install_bluez_compat(cls)
    char = cls()
    original = _dispatch(char, "ReadValue", _options(peer="/org/bluez/hci0/dev_A"))
    char.payload = b"second-peer-snapshot"
    _dispatch(char, "ReadValue", _options(peer="/org/bluez/hci0/dev_B"))
    assert _dispatch(char, "ReadValue", _options(5, "/org/bluez/hci0/dev_A")) == original[5:]
