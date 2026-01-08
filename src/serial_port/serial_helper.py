"""
串口通讯底层模块
负责与嵌入式主板进行485串口通讯
"""
import asyncio
import threading
from typing import Optional, Callable
from dataclasses import dataclass

try:
    import async_pyserial
except ImportError:
    async_pyserial = None

from ..utils.logger import get_logger


def calculate_crc16(data: bytes, start_address: int = 0) -> bytes:
    """
    CRC16-Modbus 校验计算
    多项式: 0xA001, 初始值: 0xFFFF
    
    Args:
        data: 待计算的字节数据
        start_address: 计算起始位置
        
    Returns:
        2字节CRC校验值（低字节在前，高字节在后）
    """
    polynomial = 0xA001
    crc = 0xFFFF
    for i in range(start_address, len(data)):
        crc ^= data[i] & 0xFF
        for _ in range(8):
            if crc & 0x0001:
                crc = (crc >> 1) ^ polynomial
            else:
                crc >>= 1
    # 低字节在前，高字节在后
    return bytes([crc & 0xFF, (crc >> 8) & 0xFF])


def append_crc16(data: bytes) -> bytes:
    """
    计算CRC16并追加到数据末尾
    
    Args:
        data: 原始数据
        
    Returns:
        追加CRC16后的数据
    """
    crc = calculate_crc16(data)
    return data + crc


@dataclass
class SerialResponse:
    """串口响应数据"""
    address: int          # 设备地址
    function_code: int    # 功能码
    data: bytes           # 数据部分
    raw: bytes            # 原始响应


class SerialHelper:
    """
    串口通讯辅助类 (异步版)
    
    负责：
    - 串口打开/关闭
    - 命令发送/接收
    - 数据解析
    """
    
    def __init__(self, port: str = "/dev/ttyS3", baudrate: int = 9600):
        """
        初始化串口辅助类
        
        Args:
            port: 串口设备路径
            baudrate: 波特率
        """
        self._port = port
        self._baudrate = baudrate
        self._serial = None
        self._logger = get_logger()
        self._is_open = False
        
        # 数据接收回调
        self._on_data_received: Optional[Callable[[SerialResponse], None]] = None
        self._receive_task: Optional[asyncio.Task] = None
    
    @property
    def is_open(self) -> bool:
        """串口是否已打开"""
        return self._is_open and self._serial is not None
    
    @property
    def port(self) -> str:
        return self._port
    
    @property
    def baudrate(self) -> int:
        return self._baudrate
    
    def set_on_data_received(self, callback: Callable[[SerialResponse], None]):
        """设置数据接收回调"""
        self._on_data_received = callback
    
    async def open(self) -> bool:
        """
        打开串口 (异步)
        
        Returns:
            是否成功打开
        """
        if async_pyserial is None:
            self._logger.warning("async-pyserial 未安装，串口功能不可用")
            return False
        
        if self._is_open:
            return True
        
        try:
            self._serial = async_pyserial.Serial(
                port=self._port, 
                baudrate=self._baudrate
            )
            # async-pyserial不需要显式open，实例化时通常已准备好，或者没有open方法
            # 检查是否有open方法
            if hasattr(self._serial, 'open'):
                await self._serial.open()
                
            self._is_open = True
            
            # 启动接收任务
            self._receive_task = asyncio.create_task(self._receive_loop())
            
            self._logger.info(f"串口已打开(Async): {self._port} @ {self._baudrate}")
            return True
            
        except Exception as e:
            self._logger.error(f"串口打开失败: {e}")
            self._is_open = False
            return False
    
    async def close(self):
        """关闭串口 (异步)"""
        if self._serial:
            try:
                if hasattr(self._serial, 'close'):
                    await self._serial.close()
                elif hasattr(self._serial, 'close_serial'):
                     self._serial.close_serial()
            except Exception:
                pass
            self._serial = None
        
        self._is_open = False
        
        if self._receive_task:
            self._receive_task.cancel()
            try:
                await self._receive_task
            except asyncio.CancelledError:
                pass
            self._receive_task = None
        
        self._logger.info("串口已关闭")
    
    async def send(self, data: bytes) -> bool:
        """
        发送数据 (异步)
        
        Args:
            data: 待发送的字节数据
            
        Returns:
            是否发送成功
        """
        if not self.is_open:
            return False
        
        try:
            await self._serial.write(data)
            self._logger.debug(f"发送: {data.hex().upper()}")
            return True
        except Exception as e:
            self._logger.error(f"串口发送失败: {e}")
            return False
    
    async def send_command(self, command: bytes) -> bool:
        """发送命令（自动添加CRC16校验）"""
        data = append_crc16(command)
        return await self.send(data)
    
    async def _receive_loop(self):
        """接收数据循环 (异步)"""
        buffer = bytearray()
        
        while self.is_open:
            try:
                # 异步读取
                data = await self._serial.read(1024)
                if data:
                    buffer.extend(data)
                    
                    # 解析响应
                    while len(buffer) >= 5:
                        response = self._parse_response(buffer)
                        if response:
                            if self._on_data_received:
                                try:
                                    self._on_data_received(response)
                                except Exception as e:
                                    self._logger.error(f"数据接收回调错误: {e}")
                        else:
                            if len(buffer) > 256:
                                buffer = buffer[-128:]
                            break
                else:
                    await asyncio.sleep(0.01)
                    
            except asyncio.CancelledError:
                break
            except Exception as e:
                self._logger.error(f"串口接收错误: {e}")
                await asyncio.sleep(0.1)
    
    def _parse_response(self, buffer: bytearray) -> Optional[SerialResponse]:
        """
        解析响应数据
        """
        if len(buffer) < 5:
            return None
        
        address = buffer[0]
        function_code = buffer[1]
        
        # 根据功能码确定响应长度
        if function_code == 0x03:  # 读取保持寄存器
            if len(buffer) < 3:
                return None
            data_length = buffer[2]
            total_length = 3 + data_length + 2
            if len(buffer) < total_length:
                return None
            
            raw = bytes(buffer[:total_length])
            data = bytes(buffer[3:3+data_length])
            
            if not self._verify_crc(raw):
                del buffer[0]
                return None
            
            del buffer[:total_length]
            
            return SerialResponse(
                address=address,
                function_code=function_code,
                data=data,
                raw=raw
            )
        
        elif function_code == 0x05 or function_code == 0x06:  # 写单个线圈/寄存器
            total_length = 8
            if len(buffer) < total_length:
                return None
            
            raw = bytes(buffer[:total_length])
            data = bytes(buffer[2:6])
            
            if not self._verify_crc(raw):
                del buffer[0]
                return None
            
            del buffer[:total_length]
            
            return SerialResponse(
                address=address,
                function_code=function_code,
                data=data,
                raw=raw
            )
        
        else:
            del buffer[0]
            return None
    
    def _verify_crc(self, data: bytes) -> bool:
        """验证CRC校验"""
        if len(data) < 2:
            return False
        message = data[:-2]
        received_crc = data[-2:]
        calculated_crc = calculate_crc16(message)
        return received_crc == calculated_crc
    
    # ==================== 命令构建方法 ====================
    
    def build_get_current_command(self, index: int) -> bytes:
        address = 0x01 + index
        command = bytes([address, 0x03, 0x00, 0xA0, 0x00, 0x01])
        return append_crc16(command)
    
    def build_set_relay_command(self, index: int) -> bytes:
        address = 0x01 + index
        command = bytes([address, 0x05, 0x00, 0x00, 0xFF, 0x00])
        return append_crc16(command)
    
    def build_get_lora_id_command(self) -> bytes:
        command = bytes([0x01, 0x03, 0x00, 0x30, 0x00, 0x02])
        return append_crc16(command)
    
    def build_set_lora_id_command(self, lora_id: int) -> bytes:
        command = bytes([0x01, 0x06, 0x00, 0x30, 0x00, lora_id & 0xFF])
        return append_crc16(command)
    
    def build_get_lora_channel_command(self) -> bytes:
        command = bytes([0x01, 0x03, 0x00, 0x31, 0x00, 0x02])
        return append_crc16(command)
    
    def build_set_lora_channel_command(self, channel: int) -> bytes:
        command = bytes([0x01, 0x06, 0x00, 0x31, 0x00, channel & 0xFF])
        return append_crc16(command)

from ..utils.logger import get_logger


def calculate_crc16(data: bytes, start_address: int = 0) -> bytes:
    """
    CRC16-Modbus 校验计算
    多项式: 0xA001, 初始值: 0xFFFF
    
    Args:
        data: 待计算的字节数据
        start_address: 计算起始位置
        
    Returns:
        2字节CRC校验值（低字节在前，高字节在后）
    """
    polynomial = 0xA001
    crc = 0xFFFF
    for i in range(start_address, len(data)):
        crc ^= data[i] & 0xFF
        for _ in range(8):
            if crc & 0x0001:
                crc = (crc >> 1) ^ polynomial
            else:
                crc >>= 1
    # 低字节在前，高字节在后
    return bytes([crc & 0xFF, (crc >> 8) & 0xFF])


def append_crc16(data: bytes) -> bytes:
    """
    计算CRC16并追加到数据末尾
    
    Args:
        data: 原始数据
        
    Returns:
        追加CRC16后的数据
    """
    crc = calculate_crc16(data)
    return data + crc


@dataclass
class SerialResponse:
    """串口响应数据"""
    address: int          # 设备地址
    function_code: int    # 功能码
    data: bytes           # 数据部分
    raw: bytes            # 原始响应


class SerialHelper:
    """
    串口通讯辅助类
    
    负责：
    - 串口打开/关闭
    - 命令发送/接收
    - 数据解析
    """
    
    def __init__(self, port: str = "/dev/ttyS3", baudrate: int = 9600):
        """
        初始化串口辅助类
        
        Args:
            port: 串口设备路径
            baudrate: 波特率
        """
        self._port = port
        self._baudrate = baudrate
        self._serial: Optional['serial.Serial'] = None
        self._lock = threading.Lock()
        self._logger = get_logger()
        self._is_open = False
        
        # 数据接收回调
        self._on_data_received: Optional[Callable[[SerialResponse], None]] = None
        
        # 接收线程
        self._receive_thread: Optional[threading.Thread] = None
        self._running = False
    
    @property
    def is_open(self) -> bool:
        """串口是否已打开"""
        return self._is_open and self._serial is not None
    
    @property
    def port(self) -> str:
        return self._port
    
    @property
    def baudrate(self) -> int:
        return self._baudrate
    
    def set_on_data_received(self, callback: Callable[[SerialResponse], None]):
        """设置数据接收回调"""
        self._on_data_received = callback
    
    def open(self) -> bool:
        """
        打开串口
        
        Returns:
            是否成功打开
        """
        if serial is None:
            self._logger.warning("pyserial 未安装，串口功能不可用")
            return False
        
        with self._lock:
            if self._is_open:
                return True
            
            try:
                self._serial = serial.Serial(
                    port=self._port,
                    baudrate=self._baudrate,
                    bytesize=serial.EIGHTBITS,
                    parity=serial.PARITY_NONE,
                    stopbits=serial.STOPBITS_ONE,
                    timeout=0.5
                )
                self._is_open = True
                self._running = True
                
                # 启动接收线程
                self._receive_thread = threading.Thread(
                    target=self._receive_loop, 
                    daemon=True
                )
                self._receive_thread.start()
                
                self._logger.info(f"串口已打开: {self._port} @ {self._baudrate}")
                return True
                
            except Exception as e:
                self._logger.error(f"串口打开失败: {e}")
                self._is_open = False
                return False
    
    def close(self):
        """关闭串口"""
        self._running = False
        
        with self._lock:
            if self._serial:
                try:
                    self._serial.close()
                except Exception:
                    pass
                self._serial = None
            self._is_open = False
        
        if self._receive_thread and self._receive_thread.is_alive():
            self._receive_thread.join(timeout=1.0)
        
        self._logger.info("串口已关闭")
    
    def send(self, data: bytes) -> bool:
        """
        发送数据
        
        Args:
            data: 待发送的字节数据
            
        Returns:
            是否发送成功
        """
        if not self.is_open:
            self._logger.warning("串口未打开，无法发送数据")
            return False
        
        with self._lock:
            try:
                self._serial.write(data)
                self._logger.debug(f"发送: {data.hex().upper()}")
                return True
            except Exception as e:
                self._logger.error(f"串口发送失败: {e}")
                return False
    
    def send_command(self, command: bytes) -> bool:
        """
        发送命令（自动添加CRC16校验）
        
        Args:
            command: 命令数据（不含CRC）
            
        Returns:
            是否发送成功
        """
        data = append_crc16(command)
        return self.send(data)
    
    def _receive_loop(self):
        """接收数据循环"""
        buffer = bytearray()
        
        while self._running:
            try:
                if not self.is_open:
                    time.sleep(0.1)
                    continue
                
                # 读取可用数据
                with self._lock:
                    if self._serial and self._serial.in_waiting > 0:
                        data = self._serial.read(self._serial.in_waiting)
                        buffer.extend(data)
                
                # 解析响应
                while len(buffer) >= 5:  # 最小响应长度
                    response = self._parse_response(buffer)
                    if response:
                        # 移除已解析的数据
                        if self._on_data_received:
                            try:
                                self._on_data_received(response)
                            except Exception as e:
                                self._logger.error(f"数据接收回调错误: {e}")
                    else:
                        # 如果缓冲区太大但无法解析，清除一部分防止溢出
                        if len(buffer) > 256:
                            buffer = buffer[-128:]
                        break
                
                time.sleep(0.05)
                
            except Exception as e:
                self._logger.error(f"串口接收错误: {e}")
                time.sleep(0.1)
    
    def _parse_response(self, buffer: bytearray) -> Optional[SerialResponse]:
        """
        解析响应数据
        
        Modbus RTU 响应格式:
        - 读取响应: [地址(1)] [功能码(1)] [数据长度(1)] [数据(N)] [CRC(2)]
        - 写入响应: [地址(1)] [功能码(1)] [寄存器地址(2)] [数据(2)] [CRC(2)]
        """
        if len(buffer) < 5:
            return None
        
        address = buffer[0]
        function_code = buffer[1]
        
        # 根据功能码确定响应长度
        if function_code == 0x03:  # 读取保持寄存器
            if len(buffer) < 3:
                return None
            data_length = buffer[2]
            total_length = 3 + data_length + 2  # 头(3) + 数据(N) + CRC(2)
            if len(buffer) < total_length:
                return None
            
            raw = bytes(buffer[:total_length])
            data = bytes(buffer[3:3+data_length])
            
            # 验证CRC
            if not self._verify_crc(raw):
                # CRC错误，丢弃第一个字节继续查找
                del buffer[0]
                return None
            
            # 移除已解析的数据
            del buffer[:total_length]
            
            return SerialResponse(
                address=address,
                function_code=function_code,
                data=data,
                raw=raw
            )
        
        elif function_code == 0x05 or function_code == 0x06:  # 写单个线圈/寄存器
            total_length = 8  # 固定8字节
            if len(buffer) < total_length:
                return None
            
            raw = bytes(buffer[:total_length])
            data = bytes(buffer[2:6])
            
            # 验证CRC
            if not self._verify_crc(raw):
                del buffer[0]
                return None
            
            del buffer[:total_length]
            
            return SerialResponse(
                address=address,
                function_code=function_code,
                data=data,
                raw=raw
            )
        
        else:
            # 未知功能码，丢弃第一个字节
            del buffer[0]
            return None
    
    def _verify_crc(self, data: bytes) -> bool:
        """验证CRC校验"""
        if len(data) < 2:
            return False
        message = data[:-2]
        received_crc = data[-2:]
        calculated_crc = calculate_crc16(message)
        return received_crc == calculated_crc
    
    # ==================== 命令构建方法 ====================
    
    def build_get_current_command(self, index: int) -> bytes:
        """
        构建获取电流值命令
        
        Args:
            index: 分区索引（从0开始）
            
        Returns:
            完整命令（含CRC）
        """
        address = 0x01 + index
        command = bytes([address, 0x03, 0x00, 0xA0, 0x00, 0x01])
        return append_crc16(command)
    
    def build_set_relay_command(self, index: int) -> bytes:
        """
        构建设置继电器命令（切电）
        
        Args:
            index: 分区索引（从0开始）
            
        Returns:
            完整命令（含CRC）
        """
        address = 0x01 + index
        command = bytes([address, 0x05, 0x00, 0x00, 0xFF, 0x00])
        return append_crc16(command)
    
    def build_get_lora_id_command(self) -> bytes:
        """构建获取LoRa编号命令"""
        command = bytes([0x01, 0x03, 0x00, 0x30, 0x00, 0x02])
        return append_crc16(command)
    
    def build_set_lora_id_command(self, lora_id: int) -> bytes:
        """
        构建设置LoRa编号命令
        
        Args:
            lora_id: LoRa编号
        """
        command = bytes([0x01, 0x06, 0x00, 0x30, 0x00, lora_id & 0xFF])
        return append_crc16(command)
    
    def build_get_lora_channel_command(self) -> bytes:
        """构建获取LoRa信道命令"""
        command = bytes([0x01, 0x03, 0x00, 0x31, 0x00, 0x02])
        return append_crc16(command)
    
    def build_set_lora_channel_command(self, channel: int) -> bytes:
        """
        构建设置LoRa信道命令
        
        Args:
            channel: 信道号
        """
        command = bytes([0x01, 0x06, 0x00, 0x31, 0x00, channel & 0xFF])
        return append_crc16(command)
    
    # ==================== 便捷发送方法 ====================
    
    def get_current(self, index: int) -> bool:
        """发送获取电流值命令"""
        command = self.build_get_current_command(index)
        return self.send(command)
    
    def set_relay(self, index: int) -> bool:
        """发送设置继电器命令"""
        command = self.build_set_relay_command(index)
        return self.send(command)
    
    def get_lora_id(self) -> bool:
        """发送获取LoRa编号命令"""
        command = self.build_get_lora_id_command()
        return self.send(command)
    
    def set_lora_id(self, lora_id: int) -> bool:
        """发送设置LoRa编号命令"""
        command = self.build_set_lora_id_command(lora_id)
        return self.send(command)
    
    def get_lora_channel(self) -> bool:
        """发送获取LoRa信道命令"""
        command = self.build_get_lora_channel_command()
        return self.send(command)
    
    def set_lora_channel(self, channel: int) -> bool:
        """发送设置LoRa信道命令"""
        command = self.build_set_lora_channel_command(channel)
        return self.send(command)
    
    def update_config(self, port: str = None, baudrate: int = None):
        """
        更新串口配置
        
        Args:
            port: 新的串口路径
            baudrate: 新的波特率
        """
        need_reopen = False
        
        if port is not None and port != self._port:
            self._port = port
            need_reopen = True
        
        if baudrate is not None and baudrate != self._baudrate:
            self._baudrate = baudrate
            need_reopen = True
        
        if need_reopen and self._is_open:
            self.close()
            self.open()
