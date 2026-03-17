"""
串口业务管理器
负责维护各分区电流值、动火状态判断和LoRa配置

重要设计：
- 所有串口命令通过全局队列依次执行
- 严格按照 发送->回复->发送->回复 的顺序处理
- 避免并发发送导致嵌入式设备无法处理
"""
import asyncio
import threading
import time
from typing import Dict, Optional, Callable, Any
from dataclasses import dataclass, field
from enum import Enum, auto
from concurrent.futures import Future

from .serial_helper import SerialHelper, SerialResponse
from ..utils.logger import get_logger


class CommandType(Enum):
    """串口命令类型"""
    GET_CURRENT = auto()       # 获取电流
    SET_RELAY = auto()         # 切电
    RESET_RELAY = auto()       # 复位继电器
    SET_GAS_VALVE = auto()     # 打开燃气阀（切气）
    RESET_GAS_VALVE = auto()   # 关闭燃气阀（复位）
    GET_LORA_ID = auto()       # 获取LoRa编号
    SET_LORA_ID = auto()       # 设置LoRa编号
    GET_LORA_CHANNEL = auto()  # 获取LoRa信道
    SET_LORA_CHANNEL = auto()  # 设置LoRa信道
    GET_TEMPERATURE = auto()   # 获取温度值
    SET_SENSOR_ADDRESS = auto()  # 设置传感器地址


@dataclass
class SerialCommand:
    """串口命令"""
    type: CommandType           # 命令类型
    index: Optional[int] = None # 分区索引（用于电流/继电器命令）
    value: Optional[int] = None # 值（用于设置命令）
    zone_id: Optional[str] = None  # 灶台ID（用于日志）
    future: Optional[asyncio.Future] = None  # 用于等待命令完成
    expect_response: bool = True  # 是否期待响应（写命令也有响应）


@dataclass
class ZoneCurrentInfo:
    """分区电流信息"""
    zone_id: str                    # 灶台ID
    serial_index: int               # 串口分区索引
    current_value: int = 0          # 当前电流值（整数，145=1.45A）
    fire_threshold: int = 100       # 动火阈值
    is_fire_on: bool = False        # 是否动火
    last_update: float = 0.0        # 最后更新时间
    
    # 切电复位相关
    cutoff_time: Optional[float] = None  # 切电时间戳
    can_check_reset: bool = False        # 是否可以检查电流复位（切电10秒后）


@dataclass
class LoraConfig:
    """LoRa配置"""
    id: int = 0
    channel: int = 0
    last_update: float = 0.0


@dataclass
class ZoneTemperatureInfo:
    """分区温度信息"""
    zone_id: str                    # 灶台ID
    sensor_address: int             # 温度传感器地址 (1-247)
    temperature: float = 0.0        # 当前温度 (°C)
    last_update: float = 0.0        # 最后更新时间


def parse_ieee754_float(data: bytes) -> float:
    """
    解析 IEEE754 单精度浮点数 (大端序)
    
    温度传感器返回的4字节数据按 A,B,C,D 顺序排列，
    直接组合即可还原为物理温度值。
    
    Args:
        data: 4字节数据 (IEEE754 单精度浮点数，大端序)
        
    Returns:
        解析后的浮点数温度值 (°C)
    """
    import struct
    if len(data) != 4:
        return 0.0
    # 大端序解析
    return struct.unpack('>f', data)[0]


class SerialManager:
    """
    串口业务管理器 (异步版)
    
    职责：
    - 管理串口连接（在独立线程的EventLoop中运行）
    - 通过全局命令队列依次执行所有串口操作
    - 周期性轮询电流值
    - 维护各分区电流和动火状态
    - 管理LoRa配置
    
    重要设计：
    - 所有串口命令（电流查询、切电、LoRa配置等）都通过命令队列依次执行
    - 严格保证 发送->回复->发送->回复 的顺序
    """
    
    _instance: Optional['SerialManager'] = None
    
    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance
    
    def __init__(self):
        if hasattr(self, '_initialized'):
            return
        self._initialized = True
        
        self._helper: Optional[SerialHelper] = None
        self._logger = get_logger()
        self._lock = threading.Lock()
        
        # 分区电流信息
        self._zone_currents: Dict[str, ZoneCurrentInfo] = {}
        
        # LoRa配置
        self._lora_config = LoraConfig()
        
        # 事件循环线程
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._thread: Optional[threading.Thread] = None
        self._running = False
        
        self._poll_interval = 1.0  # 轮询间隔（秒）
        self._cutoff_reset_delay = 10.0
        
        # 电流值更新回调
        self._on_current_update: Optional[Callable[[str, int, bool], None]] = None
        
        # 温度传感器信息
        self._zone_temperatures: Dict[str, ZoneTemperatureInfo] = {}
        
        # 温度值更新回调
        self._on_temperature_update: Optional[Callable[[str, float], None]] = None
        
        # ==================== 全局命令队列 ====================
        # 所有串口命令都通过这个队列依次执行
        self._command_queue: asyncio.Queue = None  # 在EventLoop中初始化
        self._current_command: Optional[SerialCommand] = None  # 当前正在执行的命令
        self._response_event: Optional[asyncio.Event] = None  # 等待响应的事件
        
        # 配置
        self._enabled = True
        self._port = "/dev/ttyS3"
        self._baudrate = 9600
    
    def initialize(self, 
                   enabled: bool = True,
                   port: str = "/dev/ttyS3", 
                   baudrate: int = 9600,
                   poll_interval: float = 1.0) -> bool:
        """
        初始化串口管理器
        
        Args:
            enabled: 是否启用串口
            port: 串口路径
            baudrate: 波特率
            poll_interval: 轮询间隔
        """
        self._enabled = enabled
        self._port = port
        self._baudrate = baudrate
        self._poll_interval = poll_interval
        
        if not enabled:
            self._logger.info("串口功能已禁用")
            return True
        
        # 启动独立线程运行EventLoop
        self._running = True
        self._thread = threading.Thread(target=self._run_event_loop, daemon=True)
        self._thread.start()
        
        # 等待Loop启动
        while self._loop is None:
            time.sleep(0.01)
            
        # 在Loop中初始化Helper和命令队列
        asyncio.run_coroutine_threadsafe(self._init_helper(), self._loop)
        
        self._logger.info(f"串口管理器初始化完成: {port} @ {baudrate}")
        return True
    
    def _run_event_loop(self):
        """运行事件循环的线程函数"""
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        self._loop = loop
        loop.run_forever()
    
    async def _init_helper(self):
        """初始化Helper (运行在Loop中)"""
        # 初始化命令队列
        self._command_queue = asyncio.Queue()
        
        self._helper = SerialHelper(self._port, self._baudrate)
        self._helper.set_on_data_received(self._on_data_received)
        
        if await self._helper.open():
            # 启动命令处理器任务
            asyncio.create_task(self._command_processor())
            # 启动轮询任务
            asyncio.create_task(self._poll_loop())
        else:
            self._logger.warning("串口打开失败(Async)")
    
    async def _command_processor(self):
        """
        命令处理器 - 核心任务
        
        从队列中取出命令，依次执行，等待响应后再处理下一个命令。
        确保所有串口操作严格按顺序执行。
        """
        self._logger.info("串口命令处理器已启动")
        
        while self._running:
            try:
                # 从队列获取命令（阻塞等待）
                command = await self._command_queue.get()
                self._current_command = command
                
                if not self._helper or not self._helper.is_open:
                    self._logger.warning("串口未打开，跳过命令")
                    if command.future and not command.future.done():
                        command.future.set_result(False)
                    continue
                
                # 创建响应等待事件
                self._response_event = asyncio.Event()
                
                # 发送命令
                success = await self._send_command(command)
                
                if success and command.expect_response:
                    # 等待响应，超时时间根据命令类型不同
                    timeout = self._get_command_timeout(command)
                    try:
                        await asyncio.wait_for(
                            self._response_event.wait(),
                            timeout=timeout
                        )
                    except asyncio.TimeoutError:
                        self._logger.warning(f"命令响应超时: {command.type.name}")
                
                # 命令完成
                if command.future and not command.future.done():
                    command.future.set_result(success)
                
                # 短暂延迟，确保设备准备好接收下一个命令
                await asyncio.sleep(0.05)
                
            except asyncio.CancelledError:
                break
            except Exception as e:
                self._logger.error(f"命令处理器错误: {e}")
            finally:
                self._current_command = None
                self._response_event = None
                self._command_queue.task_done()
        
        self._logger.info("串口命令处理器已停止")
    
    def _get_command_timeout(self, command: SerialCommand) -> float:
        """根据命令类型获取超时时间"""
        if command.type in [CommandType.GET_LORA_ID, CommandType.SET_LORA_ID,
                            CommandType.GET_LORA_CHANNEL, CommandType.SET_LORA_CHANNEL]:
            return 3.0  # LoRa命令超时较长
        elif command.type == CommandType.GET_TEMPERATURE:
            return 0.5  # 温度传感器响应时间约300ms
        elif command.type == CommandType.SET_SENSOR_ADDRESS:
            return 1.0  # 设置地址需要较长时间
        else:
            return 0.5  # 普通命令超时较短
    
    async def _send_command(self, command: SerialCommand) -> bool:
        """发送命令到串口"""
        try:
            if command.type == CommandType.GET_CURRENT:
                return await self._helper.get_current(command.index)
            
            elif command.type == CommandType.SET_RELAY:
                return await self._helper.set_relay(command.index)
            
            elif command.type == CommandType.RESET_RELAY:
                return await self._helper.reset_relay(command.index)

            elif command.type == CommandType.SET_GAS_VALVE:
                return await self._helper.set_gas_valve(command.index)

            elif command.type == CommandType.RESET_GAS_VALVE:
                return await self._helper.reset_gas_valve(command.index)

            elif command.type == CommandType.GET_LORA_ID:
                return await self._helper.get_lora_id()
            
            elif command.type == CommandType.SET_LORA_ID:
                return await self._helper.set_lora_id(command.value)
            
            elif command.type == CommandType.GET_LORA_CHANNEL:
                return await self._helper.get_lora_channel()
            
            elif command.type == CommandType.SET_LORA_CHANNEL:
                return await self._helper.set_lora_channel(command.value)
            
            elif command.type == CommandType.GET_TEMPERATURE:
                return await self._helper.get_temperature(command.index)
            
            elif command.type == CommandType.SET_SENSOR_ADDRESS:
                # command.index = old_address, command.value = new_address
                return await self._helper.set_sensor_address(command.index, command.value)
            
            else:
                self._logger.warning(f"未知命令类型: {command.type}")
                return False
                
        except Exception as e:
            self._logger.error(f"发送命令失败: {e}")
            return False
    
    async def _enqueue_command(self, command: SerialCommand) -> bool:
        """
        将命令加入队列
        
        Args:
            command: 要执行的命令
            
        Returns:
            命令是否成功加入队列
        """
        if self._command_queue is None:
            self._logger.warning("命令队列未初始化")
            return False
        
        await self._command_queue.put(command)
        return True
    
    def _enqueue_command_sync(self, command: SerialCommand) -> bool:
        """
        同步方式将命令加入队列（从外部线程调用）
        
        Args:
            command: 要执行的命令
            
        Returns:
            命令是否成功提交
        """
        if self._loop is None or self._command_queue is None:
            return False
        
        asyncio.run_coroutine_threadsafe(
            self._command_queue.put(command),
            self._loop
        )
        return True
    
    def register_zone(self, zone_id: str, serial_index: int, fire_threshold: int):
        """注册分区"""
        with self._lock:
            self._zone_currents[zone_id] = ZoneCurrentInfo(
                zone_id=zone_id,
                serial_index=serial_index,
                fire_threshold=fire_threshold
            )
        self._logger.info(f"注册分区: {zone_id}, 索引: {serial_index}, 阈值: {fire_threshold}")
    
    def unregister_zone(self, zone_id: str):
        """注销分区"""
        with self._lock:
            if zone_id in self._zone_currents:
                del self._zone_currents[zone_id]
        self._logger.info(f"注销分区: {zone_id}")
    
    def update_zone_config(self, zone_id: str, serial_index: int = None, fire_threshold: int = None):
        """更新分区配置"""
        with self._lock:
            if zone_id in self._zone_currents:
                info = self._zone_currents[zone_id]
                if serial_index is not None:
                    info.serial_index = serial_index
                if fire_threshold is not None:
                    info.fire_threshold = fire_threshold
    
    def set_on_current_update(self, callback: Callable[[str, int, bool], None]):
        """设置电流更新回调"""
        self._on_current_update = callback
    
    async def _poll_loop(self):
        """轮询循环 (Async Task)"""
        self._logger.info("串口轮询任务已启动")
        
        # 初始获取LoRa配置
        await asyncio.sleep(0.5)
        await self._request_lora_internal()
        
        while self._running:
            try:
                if not self._helper or not self._helper.is_open:
                    await asyncio.sleep(1)
                    continue
                
                # 轮询所有分区电流值（通过命令队列依次执行）
                with self._lock:
                    zones = list(self._zone_currents.values())
                
                for zone_info in zones:
                    if not self._running:
                        break
                    
                    # 创建获取电流命令并加入队列
                    command = SerialCommand(
                        type=CommandType.GET_CURRENT,
                        index=zone_info.serial_index,
                        zone_id=zone_info.zone_id,
                        expect_response=True
                    )
                    await self._enqueue_command(command)
                    
                    # 检查是否可以进行电流复位判断
                    if zone_info.cutoff_time is not None:
                        elapsed = time.time() - zone_info.cutoff_time
                        if elapsed >= self._cutoff_reset_delay:
                            zone_info.can_check_reset = True
                
                # 轮询所有分区温度值（通过命令队列依次执行）
                with self._lock:
                    temp_zones = list(self._zone_temperatures.values())
                
                for temp_info in temp_zones:
                    if not self._running:
                        break
                    
                    # 创建获取温度命令并加入队列
                    command = SerialCommand(
                        type=CommandType.GET_TEMPERATURE,
                        index=temp_info.sensor_address,
                        zone_id=temp_info.zone_id,
                        expect_response=True
                    )
                    await self._enqueue_command(command)
                
                # 等待队列中的所有命令完成
                await self._command_queue.join()
                
                await asyncio.sleep(self._poll_interval)
                
            except Exception as e:
                self._logger.error(f"轮询错误: {e}")
                await asyncio.sleep(1)
        
        self._logger.info("串口轮询任务已停止")
    
    async def _request_lora_internal(self):
        """请求LoRa配置（内部使用，通过命令队列）"""
        # 获取编号
        cmd1 = SerialCommand(type=CommandType.GET_LORA_ID, expect_response=True)
        await self._enqueue_command(cmd1)
        
        # 等待一下
        await asyncio.sleep(0.1)
        
        # 获取信道
        cmd2 = SerialCommand(type=CommandType.GET_LORA_CHANNEL, expect_response=True)
        await self._enqueue_command(cmd2)
    
    async def request_lora_config(self):
        """请求LoRa配置（供外部调用）"""
        if self._helper and self._helper.is_open:
            await self._request_lora_internal()
    
    def _on_data_received(self, response: SerialResponse):
        """处理串口响应 (Called from Loop)"""
        address = response.address
        function_code = response.function_code
        data = response.data
        
        current_cmd = self._current_command
        
        if function_code == 0x03:  # 读取响应
            if len(data) >= 2:
                value = (data[0] << 8) | data[1]
                
                # 根据当前命令类型处理响应
                if current_cmd:
                    if current_cmd.type == CommandType.GET_LORA_ID:
                        self._lora_config.id = value
                        self._lora_config.last_update = time.time()
                        self._logger.info(f"LoRa编号: {value}")
                    elif current_cmd.type == CommandType.GET_LORA_CHANNEL:
                        self._lora_config.channel = value
                        self._lora_config.last_update = time.time()
                        self._logger.info(f"LoRa信道: {value}")
                    elif current_cmd.type == CommandType.GET_CURRENT:
                        self._update_current(address, value)
                    elif current_cmd.type == CommandType.GET_TEMPERATURE:
                        # 温度传感器返回4字节IEEE754浮点数
                        if len(data) >= 4:
                            temperature = parse_ieee754_float(data[:4])
                            self._update_temperature(address, temperature)
                    else:
                        # 可能是设置LoRa后的读取确认
                        if address == 0x01:
                            # 根据上下文判断是ID还是Channel
                            pass
                else:
                    # 没有当前命令，尝试通过地址判断
                    self._update_current(address, value)
        
        elif function_code == 0x05:
            self._logger.info(f"继电器操作成功: addr={address}")
        
        elif function_code == 0x06:
            self._logger.info(f"寄存器写入成功: addr={address}")
        
        # 触发响应事件，通知命令处理器
        if self._response_event:
            self._response_event.set()
    
    def _update_current(self, address: int, value: int):
        """更新电流值"""
        serial_index = address
        
        with self._lock:
            for zone_id, info in self._zone_currents.items():
                if info.serial_index == serial_index:
                    old_fire_on = info.is_fire_on
                    info.current_value = value
                    info.is_fire_on = value > info.fire_threshold
                    info.last_update = time.time()
                    
                    if info.is_fire_on != old_fire_on:
                        state_text = "动火" if info.is_fire_on else "熄火"
                        self._logger.info(f"[{zone_id}] 电流: {value}, 状态: {state_text}")
                    
                    if self._on_current_update:
                        try:
                            self._on_current_update(zone_id, value, info.is_fire_on)
                        except Exception as e:
                            self._logger.error(f"电流更新回调错误: {e}")
                    break
    
    def _update_temperature(self, address: int, temperature: float):
        """更新温度值"""
        with self._lock:
            for zone_id, info in self._zone_temperatures.items():
                if info.sensor_address == address:
                    info.temperature = temperature
                    info.last_update = time.time()
                    
                    self._logger.debug(f"[{zone_id}] 温度: {temperature:.1f}°C")
                    
                    if self._on_temperature_update:
                        try:
                            self._on_temperature_update(zone_id, temperature)
                        except Exception as e:
                            self._logger.error(f"温度更新回调错误: {e}")
                    break
    
    # ==================== 温度传感器接口 ====================
    
    def register_temperature_sensor(self, zone_id: str, sensor_address: int):
        """注册温度传感器"""
        with self._lock:
            self._zone_temperatures[zone_id] = ZoneTemperatureInfo(
                zone_id=zone_id,
                sensor_address=sensor_address
            )
        self._logger.info(f"注册温度传感器: {zone_id}, 地址: {sensor_address}")
    
    def unregister_temperature_sensor(self, zone_id: str):
        """注销温度传感器"""
        with self._lock:
            if zone_id in self._zone_temperatures:
                del self._zone_temperatures[zone_id]
        self._logger.info(f"注销温度传感器: {zone_id}")
    
    def update_temperature_sensor_config(self, zone_id: str, sensor_address: int):
        """更新温度传感器配置"""
        with self._lock:
            if zone_id in self._zone_temperatures:
                self._zone_temperatures[zone_id].sensor_address = sensor_address
    
    def set_on_temperature_update(self, callback: Callable[[str, float], None]):
        """设置温度更新回调"""
        self._on_temperature_update = callback
    
    def get_temperature(self, zone_id: str) -> float:
        """获取分区温度值"""
        with self._lock:
            if zone_id in self._zone_temperatures:
                return self._zone_temperatures[zone_id].temperature
        return 0.0
    
    def get_all_temperatures(self) -> Dict[str, float]:
        """获取所有分区温度值"""
        with self._lock:
            return {
                zone_id: info.temperature 
                for zone_id, info in self._zone_temperatures.items()
            }
    
    def get_used_sensor_addresses(self) -> set:
        """获取已使用的传感器地址"""
        with self._lock:
            return {info.sensor_address for info in self._zone_temperatures.values()}
    
    def allocate_sensor_address(self) -> int:
        """
        分配一个新的传感器地址
        
        从 1 开始，跳过已使用的地址和保留地址 (123, 200)
        
        Returns:
            新分配的地址 (1-247，跳过123和200)
        """
        used = self.get_used_sensor_addresses()
        reserved = {123, 200}  # 123=默认地址, 200=万能地址
        
        for addr in range(1, 248):
            if addr not in used and addr not in reserved:
                return addr
        
        # 如果所有地址都用完了，返回0表示无可用地址
        return 0
    
    def assign_sensor_address(self, old_address: int, new_address: int) -> bool:
        """
        为传感器分配新地址 (通过命令队列执行)
        
        Args:
            old_address: 当前传感器地址 (通常是123或200)
            new_address: 新的传感器地址
            
        Returns:
            是否成功提交命令
        """
        if self._loop and self._command_queue is not None:
            asyncio.run_coroutine_threadsafe(
                self._enqueue_set_sensor_address(old_address, new_address),
                self._loop
            )
            return True
        return False
    
    async def _enqueue_set_sensor_address(self, old_address: int, new_address: int):
        """将设置传感器地址命令加入队列"""
        cmd = SerialCommand(
            type=CommandType.SET_SENSOR_ADDRESS,
            index=old_address,
            value=new_address,
            expect_response=True
        )
        await self._enqueue_command(cmd)
        self._logger.info(f"传感器地址修改命令已加入队列: {old_address} -> {new_address}")
    
    # ==================== 所谓"同步"接口 (读缓存) ====================
    
    def get_current(self, zone_id: str) -> int:
        with self._lock:
            if zone_id in self._zone_currents:
                return self._zone_currents[zone_id].current_value
        return 0
    
    def get_all_currents(self) -> Dict[str, int]:
        with self._lock:
            return {
                zone_id: info.current_value 
                for zone_id, info in self._zone_currents.items()
            }
    
    def is_fire_on(self, zone_id: str) -> bool:
        with self._lock:
            if zone_id in self._zone_currents:
                return self._zone_currents[zone_id].is_fire_on
        return False
        
    def get_zone_info(self, zone_id: str) -> Optional[Dict]:
        with self._lock:
            if zone_id in self._zone_currents:
                info = self._zone_currents[zone_id]
                return {
                    "zone_id": info.zone_id,
                    "serial_index": info.serial_index,
                    "current_value": info.current_value,
                    "fire_threshold": info.fire_threshold,
                    "is_fire_on": info.is_fire_on,
                    "last_update": info.last_update
                }
        return None

    def can_reset_by_current(self, zone_id: str) -> bool:
        with self._lock:
            if zone_id not in self._zone_currents:
                return False
            info = self._zone_currents[zone_id]
            return info.can_check_reset and info.is_fire_on
    
    def clear_cutoff_state(self, zone_id: str):
        with self._lock:
            if zone_id in self._zone_currents:
                info = self._zone_currents[zone_id]
                info.cutoff_time = None
                info.can_check_reset = False
                
    def get_lora_config(self) -> Dict:
        return {
            "id": self._lora_config.id,
            "channel": self._lora_config.channel
        }

    def get_serial_config(self) -> Dict:
        is_open = False
        debug_hex = False
        queue_size = 0
        # thread-safe check
        if self._helper:
            is_open = self._helper.is_open
            debug_hex = self._helper.get_debug_hex()
        if self._command_queue:
            queue_size = self._command_queue.qsize()
            
        return {
            "enabled": self._enabled,
            "port": self._port,
            "baudrate": self._baudrate,
            "poll_interval": self._poll_interval,
            "is_open": is_open,
            "debug_hex": debug_hex,
            "queue_size": queue_size  # 新增：队列中等待的命令数
        }
    
    def set_debug_hex(self, enabled: bool) -> bool:
        """
        设置16进制调试日志开关
        
        Args:
            enabled: True开启调试日志，False关闭
            
        Returns:
            是否设置成功
        """
        if self._helper:
            self._helper.set_debug_hex(enabled)
            return True
        return False
    
    def get_debug_hex(self) -> bool:
        """获取16进制调试日志状态"""
        if self._helper:
            return self._helper.get_debug_hex()
        return False

    # ==================== 控制接口 (通过命令队列执行) ====================
    
    def cutoff(self, zone_id: str) -> bool:
        """
        执行切电 (Sync, Non-blocking)
        
        切电操作通过命令队列执行，保证与其他串口操作顺序执行。
        """
        with self._lock:
            if zone_id not in self._zone_currents:
                return False
            info = self._zone_currents[zone_id]
            info.cutoff_time = time.time()
            info.can_check_reset = False
            serial_index = info.serial_index
        
        # 将切电和复位命令加入队列
        if self._loop and self._command_queue is not None:
            asyncio.run_coroutine_threadsafe(
                self._enqueue_cutoff_commands(serial_index, zone_id),
                self._loop
            )
            return True
        return False
    
    async def _enqueue_cutoff_commands(self, serial_index: int, zone_id: str):
        """将切电和切气命令加入队列"""
        # 1. 切电命令
        cmd1 = SerialCommand(
            type=CommandType.SET_RELAY,
            index=serial_index,
            zone_id=zone_id,
            expect_response=True
        )
        await self._enqueue_command(cmd1)
        self._logger.info(f"[{zone_id}] 切电命令已加入队列")

        # 2. 切气命令（打开燃气阀）
        cmd2 = SerialCommand(
            type=CommandType.SET_GAS_VALVE,
            index=0,  # position 固定为0
            zone_id=zone_id,
            expect_response=True
        )
        await self._enqueue_command(cmd2)
        self._logger.info(f"[{zone_id}] 切气命令已加入队列")

        # 3. 等待100ms后发送切电复位命令
        await asyncio.sleep(0.1)

        # 4. 切电复位命令
        cmd3 = SerialCommand(
            type=CommandType.RESET_RELAY,
            index=serial_index,
            zone_id=zone_id,
            expect_response=True
        )
        await self._enqueue_command(cmd3)
        self._logger.info(f"[{zone_id}] 切电复位命令已加入队列")

        # 5. 等待1秒后发送切气复位命令
        await asyncio.sleep(1.0)

        # 6. 切气复位命令（关闭燃气阀）
        cmd4 = SerialCommand(
            type=CommandType.RESET_GAS_VALVE,
            index=0,  # position 固定为0
            zone_id=zone_id,
            expect_response=True
        )
        await self._enqueue_command(cmd4)
        self._logger.info(f"[{zone_id}] 切气复位命令已加入队列")

    def set_lora_id(self, lora_id: int) -> bool:
        """设置LoRa编号 (通过命令队列执行)"""
        if self._loop and self._command_queue is not None:
            asyncio.run_coroutine_threadsafe(
                self._enqueue_set_lora_id(lora_id),
                self._loop
            )
            return True
        return False

    async def _enqueue_set_lora_id(self, lora_id: int):
        """将设置LoRa编号命令加入队列"""
        # 设置命令
        cmd1 = SerialCommand(
            type=CommandType.SET_LORA_ID,
            value=lora_id,
            expect_response=True
        )
        await self._enqueue_command(cmd1)
        
        # 等待一下再读取确认
        await asyncio.sleep(0.3)
        
        # 读取确认
        cmd2 = SerialCommand(
            type=CommandType.GET_LORA_ID,
            expect_response=True
        )
        await self._enqueue_command(cmd2)

    def set_lora_channel(self, channel: int) -> bool:
        """设置LoRa信道 (通过命令队列执行)"""
        if self._loop and self._command_queue is not None:
            asyncio.run_coroutine_threadsafe(
                self._enqueue_set_lora_channel(channel),
                self._loop
            )
            return True
        return False

    async def _enqueue_set_lora_channel(self, channel: int):
        """将设置LoRa信道命令加入队列"""
        # 设置命令
        cmd1 = SerialCommand(
            type=CommandType.SET_LORA_CHANNEL,
            value=channel,
            expect_response=True
        )
        await self._enqueue_command(cmd1)
        
        # 等待一下再读取确认
        await asyncio.sleep(0.3)
        
        # 读取确认
        cmd2 = SerialCommand(
            type=CommandType.GET_LORA_CHANNEL,
            expect_response=True
        )
        await self._enqueue_command(cmd2)

    def update_serial_config(self, 
                             enabled: bool = None,
                             port: str = None, 
                             baudrate: int = None,
                             poll_interval: float = None) -> bool:
        """
        更新配置
        
        只有当配置真正改变时才触发串口重启，避免不必要的重新初始化。
        """
        need_restart = False
        
        # 只有值真正改变时才设置 need_restart
        if enabled is not None and enabled != self._enabled:
            self._enabled = enabled
            need_restart = True
        if port is not None and port != self._port:
            self._port = port
            need_restart = True
        if baudrate is not None and baudrate != self._baudrate:
            self._baudrate = baudrate
            need_restart = True
        if poll_interval is not None and poll_interval != self._poll_interval:
            self._poll_interval = poll_interval
            # poll_interval 改变不需要重启串口
            
        if need_restart:
            self._logger.info(f"串口配置已更改，正在重新初始化...")
            self.stop()
            time.sleep(0.5)
            self.initialize(
                self._enabled,
                self._port,
                self._baudrate,
                self._poll_interval
            )
        else:
            self._logger.info("串口配置未改变，无需重新初始化")
        return True

    def stop(self):
        """
        停止串口管理器
        
        正确关闭串口连接、停止事件循环、清理所有状态，
        以便后续可以重新初始化。
        """
        self._running = False
        
        # 先关闭串口
        if self._loop and self._helper:
            try:
                future = asyncio.run_coroutine_threadsafe(
                    self._helper.close(),
                    self._loop
                )
                future.result(timeout=2.0)
            except Exception as e:
                self._logger.warning(f"关闭串口时出错: {e}")
        
        # 停止事件循环
        if self._loop:
            try:
                self._loop.call_soon_threadsafe(self._loop.stop)
            except Exception:
                pass
        
        # 等待线程结束
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=2.0)
        
        # 重置所有状态，以便重新初始化
        self._helper = None
        self._loop = None
        self._thread = None
        self._command_queue = None
        self._current_command = None
        self._response_event = None
        
        self._logger.info("串口管理器已停止")


# 全局串口管理器实例
serial_manager = SerialManager()
