"""bless 0.3.0 BlueZ 适配：保留 MTU options，按同一快照处理长 Read Blob。

该版本的 D-Bus ReadValue/WriteValue 会丢弃 options。dbus-next 调度使用
__DBUS_METHOD.fn（直接替换 Python 装饰器包装函数不会影响 D-Bus 调度）。
仅给绑定了 _dhlr_options_observer 的本服务特征启用，其他服务保持原行为。
"""
from __future__ import annotations

import logging
import time

logger = logging.getLogger(__name__)
READ_SNAPSHOT_TTL_SEC = 30


def option_value(options: dict, key: str, default=None):
    value = options.get(key, default)
    return getattr(value, "value", value)


def _read_value(characteristic, options: dict, original_read):
    observer = getattr(characteristic, "_dhlr_options_observer", None)
    if observer is None:
        return original_read(characteristic, options)
    observer(options)
    offset = option_value(options, "offset", 0)
    if type(offset) is not int or offset < 0:
        from dbus_next import DBusError
        raise DBusError("org.bluez.Error.InvalidOffset", "Invalid read offset")
    peer = str(option_value(options, "device", "unknown"))
    snapshots = getattr(characteristic, "_dhlr_read_snapshots", None)
    if snapshots is None:
        snapshots = {}
        characteristic._dhlr_read_snapshots = snapshots
    now = time.monotonic()
    snapshot = snapshots.get(peer)
    if offset == 0 or snapshot is None or now - snapshot[0] > READ_SNAPSHOT_TTL_SEC:
        # 第一个 read 拍快照，后续 blob 不能因网络状态缓存变化而换掉 JSON 内容。
        snapshot = (now, bytes(original_read(characteristic, options)))
        if len(snapshots) >= 16 and peer not in snapshots:
            snapshots.pop(next(iter(snapshots)))
        snapshots[peer] = snapshot
    value = snapshot[1]
    if offset > len(value):
        from dbus_next import DBusError
        raise DBusError("org.bluez.Error.InvalidOffset", "Read offset exceeds value length")
    logger.debug("[read] char=%s offset=%d total=%d", getattr(characteristic, "UUID", "?"), offset, len(value))
    return value[offset:]


def install_bluez_compat(characteristic_type=None) -> bool:
    """在注册 GATT 前安装；可重复调用，签名/错误语义不变。"""
    if characteristic_type is None:
        try:
            from bless.backends.bluezdbus.dbus.characteristic import BlueZGattCharacteristic
            characteristic_type = BlueZGattCharacteristic
        except ImportError:
            logger.warning("无 bless BlueZ 后端，通知使用保守 MTU，未安装长读取适配")
            return False
    read = getattr(characteristic_type.ReadValue, "__DBUS_METHOD", None)
    write = getattr(characteristic_type.WriteValue, "__DBUS_METHOD", None)
    if read is None or write is None:
        raise RuntimeError("bless/dbus-next 接口变化，无法安装 BLE options 适配")
    if getattr(read.fn, "_dhlr_compat", False):
        return True
    original_read, original_write = read.fn, write.fn

    def read_value(characteristic, options):
        return _read_value(characteristic, options, original_read)

    def write_value(characteristic, value, options):
        observer = getattr(characteristic, "_dhlr_options_observer", None)
        if observer is not None:
            observer(options)
        return original_write(characteristic, value, options)

    read_value._dhlr_compat = True
    read.fn = read_value
    write.fn = write_value
    logger.info("已安装 bless 0.3.0 BlueZ MTU/长读取适配")
    return True
