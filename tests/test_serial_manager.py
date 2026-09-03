"""
测试 serial_port/serial_manager.py 的电流查询无响应置零逻辑

测试内容:
- 连续3次电流查询无响应后电流值置零、状态熄火
- 中途收到响应会清零连续无响应计数
- 已置零后继续无响应不重复触发
- 置零只影响对应串口索引的分区
- 收到电流响应会标记命令 response_received
"""
import pytest
from unittest.mock import MagicMock

from src.serial_port.serial_helper import SerialResponse
from src.serial_port.serial_manager import (
    SerialManager,
    SerialCommand,
    CommandType,
)


def make_current_response(address: int, value: int) -> SerialResponse:
    """构造电流读取响应: [地址][0x03][字节数=2][高字节][低字节][CRC]"""
    data = bytes([(value >> 8) & 0xFF, value & 0xFF])
    return SerialResponse(
        address=address,
        function_code=0x03,
        data=data,
        raw=bytes([address, 0x03, 0x02]) + data + b'\x00\x00',
    )


@pytest.fixture
def manager():
    """提供隔离的 SerialManager 实例（不启动串口线程）"""
    original = SerialManager._instance
    SerialManager._instance = None
    m = SerialManager()
    m._logger = MagicMock()
    yield m
    SerialManager._instance = original


class TestCurrentMissZeroing:
    """连续无响应置零逻辑"""

    def test_zeroed_after_three_consecutive_misses(self, manager):
        """连续3次无响应后电流置零并熄火"""
        manager.register_zone("z1", 1, 100)
        callback = MagicMock()
        manager.set_on_current_update(callback)

        # 先建立正常动火状态
        manager._update_current(1, 145)
        assert manager.get_current("z1") == 145
        assert manager.is_fire_on("z1")

        cmd = SerialCommand(type=CommandType.GET_CURRENT, index=1)
        manager._handle_current_miss(cmd)
        manager._handle_current_miss(cmd)
        # 前2次不置零
        assert manager.get_current("z1") == 145

        manager._handle_current_miss(cmd)
        # 第3次置零并熄火
        assert manager.get_current("z1") == 0
        assert not manager.is_fire_on("z1")

        # 回调共触发2次: 动火(145, True) 和 置零(0, False)
        assert callback.call_count == 2
        callback.assert_called_with("z1", 0, False)

    def test_response_resets_miss_count(self, manager):
        """收到响应后连续计数清零，不会累积置零"""
        manager.register_zone("z1", 1, 100)
        cmd = SerialCommand(type=CommandType.GET_CURRENT, index=1)

        manager._handle_current_miss(cmd)
        manager._handle_current_miss(cmd)
        manager._update_current(1, 145)  # 响应打断连续无响应
        manager._handle_current_miss(cmd)
        manager._handle_current_miss(cmd)

        assert manager.get_current("z1") == 145

    def test_no_repeat_after_zeroed(self, manager):
        """置零后继续无响应不重复触发"""
        manager.register_zone("z1", 1, 100)
        callback = MagicMock()
        manager.set_on_current_update(callback)

        manager._update_current(1, 145)
        cmd = SerialCommand(type=CommandType.GET_CURRENT, index=1)
        for _ in range(6):
            manager._handle_current_miss(cmd)

        assert callback.call_count == 2  # 动火 + 置零各一次
        callback.assert_called_with("z1", 0, False)

    def test_other_index_unaffected(self, manager):
        """置零只影响同一串口索引的分区"""
        manager.register_zone("z1", 1, 100)
        manager.register_zone("z2", 2, 100)
        manager._update_current(1, 145)
        manager._update_current(2, 160)

        cmd = SerialCommand(type=CommandType.GET_CURRENT, index=1)
        for _ in range(3):
            manager._handle_current_miss(cmd)

        assert manager.get_current("z1") == 0
        assert manager.get_current("z2") == 160

    def test_last_update_refreshed_after_zeroing(self, manager):
        """置零后刷新最后更新时间"""
        import time as _time
        manager.register_zone("z1", 1, 100)
        manager._update_current(1, 145)
        before = _time.time()
        _time.sleep(0.01)

        cmd = SerialCommand(type=CommandType.GET_CURRENT, index=1)
        for _ in range(3):
            manager._handle_current_miss(cmd)

        assert manager.get_zone_info("z1")["last_update"] >= before


class TestCurrentResponseReceived:
    """电流响应的 response_received 标记"""

    def test_response_marks_command_received(self, manager):
        """收到电流响应时标记命令已响应（无响应检测的依据）"""
        manager.register_zone("z1", 1, 100)
        cmd = SerialCommand(type=CommandType.GET_CURRENT, index=1, zone_id="z1")
        manager._current_command = cmd

        manager._on_data_received(make_current_response(1, 145))

        assert cmd.response_received
        assert manager.get_current("z1") == 145
