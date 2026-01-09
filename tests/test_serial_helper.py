"""
测试 serial_port/serial_helper.py 模块

测试内容:
- CRC16-Modbus 校验计算
- CRC16 追加
- 命令构建方法
"""
import pytest

from src.serial_port.serial_helper import calculate_crc16, append_crc16


class TestCRC16:
    """测试 CRC16-Modbus 计算"""
    
    def test_known_crc_value(self):
        """使用已知的 Modbus 命令验证 CRC 计算"""
        # 读取保持寄存器命令: 01 03 00 A0 00 01
        # CRC 由 calculate_crc16 计算得出
        data = bytes([0x01, 0x03, 0x00, 0xA0, 0x00, 0x01])
        crc = calculate_crc16(data)
        assert len(crc) == 2
        assert crc == bytes([0x84, 0x28])
    
    def test_another_known_crc(self):
        """另一个已知 CRC 测试"""
        # 写单个线圈命令: 01 05 00 00 FF 00
        # 预期 CRC: 8C 3A
        data = bytes([0x01, 0x05, 0x00, 0x00, 0xFF, 0x00])
        crc = calculate_crc16(data)
        assert crc == bytes([0x8C, 0x3A])
    
    def test_empty_data(self):
        """测试空数据"""
        crc = calculate_crc16(b'')
        # 空数据的 CRC 应该是初始值 0xFFFF
        assert len(crc) == 2
        assert crc == bytes([0xFF, 0xFF])
    
    def test_single_byte(self):
        """测试单字节数据"""
        crc = calculate_crc16(bytes([0x01]))
        assert len(crc) == 2
        # 验证返回的是 bytes 类型
        assert isinstance(crc, bytes)
    
    def test_start_address(self):
        """测试 start_address 参数"""
        data = bytes([0x00, 0x01, 0x03, 0x00, 0xA0, 0x00, 0x01])
        # 从索引1开始计算，应该和直接计算后6个字节相同
        crc_with_offset = calculate_crc16(data, start_address=1)
        crc_direct = calculate_crc16(data[1:])
        assert crc_with_offset == crc_direct


class TestAppendCRC16:
    """测试 append_crc16 函数"""
    
    def test_append_crc(self):
        """测试 CRC 追加"""
        data = bytes([0x01, 0x03, 0x00, 0xA0, 0x00, 0x01])
        result = append_crc16(data)
        
        # 结果应该比原数据多2字节
        assert len(result) == len(data) + 2
        # 前面部分应该和原数据相同
        assert result[:len(data)] == data
        # 最后2字节是 CRC
        assert result[-2:] == bytes([0x84, 0x28])
    
    def test_append_crc_empty(self):
        """测试空数据追加 CRC"""
        result = append_crc16(b'')
        assert len(result) == 2
        assert result == bytes([0xFF, 0xFF])


class TestCommandBuilder:
    """测试命令构建方法"""
    
    @pytest.fixture
    def helper(self):
        """创建 SerialHelper 实例（仅用于命令构建）"""
        from src.serial_port.serial_helper import SerialHelper
        # 使用模拟端口，不会真正打开
        return SerialHelper(port="/dev/null", baudrate=9600)
    
    def test_build_get_current_command_index_0(self, helper):
        """测试构建获取电流命令 (索引0)"""
        cmd = helper.build_get_current_command(0)
        
        # 地址: 0x01, 功能码: 0x03, 起始地址: 0x00A0, 数量: 0x0001
        expected_base = bytes([0x01, 0x03, 0x00, 0xA0, 0x00, 0x01])
        expected = append_crc16(expected_base)
        
        assert cmd == expected
        assert len(cmd) == 8  # 6字节命令 + 2字节CRC
    
    def test_build_get_current_command_index_1(self, helper):
        """测试构建获取电流命令 (索引1)"""
        cmd = helper.build_get_current_command(1)
        
        # 地址应该是 0x02
        assert cmd[0] == 0x02
        assert cmd[1] == 0x03  # 功能码
    
    def test_build_set_relay_command(self, helper):
        """测试构建设置继电器命令"""
        cmd = helper.build_set_relay_command(0)
        
        # 地址: 0x01, 功能码: 0x05, 线圈地址: 0x0000, 值: 0xFF00
        expected_base = bytes([0x01, 0x05, 0x00, 0x00, 0xFF, 0x00])
        expected = append_crc16(expected_base)
        
        assert cmd == expected
    
    def test_build_get_lora_id_command(self, helper):
        """测试构建获取 LoRa ID 命令"""
        cmd = helper.build_get_lora_id_command()
        
        assert len(cmd) == 8
        assert cmd[0] == 0x01  # 地址
        assert cmd[1] == 0x03  # 功能码 (读取保持寄存器)
    
    def test_build_set_lora_id_command(self, helper):
        """测试构建设置 LoRa ID 命令"""
        lora_id = 42
        cmd = helper.build_set_lora_id_command(lora_id)
        
        assert len(cmd) == 8
        assert cmd[0] == 0x01  # 地址
        assert cmd[1] == 0x06  # 功能码 (写单个寄存器)
        assert cmd[5] == lora_id  # LoRa ID 值
    
    def test_build_set_lora_channel_command(self, helper):
        """测试构建设置 LoRa 信道命令"""
        channel = 15
        cmd = helper.build_set_lora_channel_command(channel)
        
        assert cmd[5] == channel
    
    def test_lora_id_overflow(self, helper):
        """测试 LoRa ID 溢出处理"""
        # 传入超过 0xFF 的值，应该被截断
        cmd = helper.build_set_lora_id_command(256)
        assert cmd[5] == 0x00  # 256 & 0xFF = 0
        
        cmd = helper.build_set_lora_id_command(257)
        assert cmd[5] == 0x01  # 257 & 0xFF = 1


class TestCRCVerification:
    """测试 CRC 验证"""
    
    def test_valid_crc(self):
        """测试有效 CRC 验证"""
        from src.serial_port.serial_helper import SerialHelper
        helper = SerialHelper(port="/dev/null")
        
        # 有效的数据包 (使用实际 CRC)
        valid_data = bytes([0x01, 0x03, 0x00, 0xA0, 0x00, 0x01, 0x84, 0x28])
        assert helper._verify_crc(valid_data) is True
    
    def test_invalid_crc(self):
        """测试无效 CRC 验证"""
        from src.serial_port.serial_helper import SerialHelper
        helper = SerialHelper(port="/dev/null")
        
        # 无效的数据包（CRC 错误）
        invalid_data = bytes([0x01, 0x03, 0x00, 0xA0, 0x00, 0x01, 0x00, 0x00])
        assert helper._verify_crc(invalid_data) is False
    
    def test_too_short_data(self):
        """测试过短数据"""
        from src.serial_port.serial_helper import SerialHelper
        helper = SerialHelper(port="/dev/null")
        
        assert helper._verify_crc(b'') is False
        assert helper._verify_crc(bytes([0x01])) is False
