"""
串口业务管理器
负责维护各分区电流值、动火状态判断和LoRa配置
"""
import asyncio
import threading
import time
from typing import Dict, Optional, Callable, Any
from dataclasses import dataclass, field
from concurrent.futures import Future

from .serial_helper import SerialHelper, SerialResponse
from ..utils.logger import get_logger


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


class SerialManager:
    """
    串口业务管理器 (异步版)
    
    职责：
    - 管理串口连接（在独立线程的EventLoop中运行）
    - 周期性轮询电流值
    - 维护各分区电流和动火状态
    - 管理LoRa配置
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
        
        # 等待LoRa响应的队列 (FIFO)
        # 元素为 'id' 或 'channel'，表示期待的响应类型
        self._lora_response_queue: list[str] = []
        
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
            
        # 在Loop中初始化Helper
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
        self._helper = SerialHelper(self._port, self._baudrate)
        self._helper.set_on_data_received(self._on_data_received)
        
        if await self._helper.open():
            # 启动轮询任务
            asyncio.create_task(self._poll_loop())
        else:
            self._logger.warning("串口打开失败(Async)")
    
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
        self._request_lora_config()
        
        while self._running:
            try:
                if not self._helper or not self._helper.is_open:
                    await asyncio.sleep(1)
                    continue
                
                # 轮询所有分区电流值
                with self._lock:
                    zones = list(self._zone_currents.values())
                
                for zone_info in zones:
                    if not self._running:
                        break
                    
                    await self._helper.get_current(zone_info.serial_index)
                    await asyncio.sleep(0.2)  # 命令间隔
                    
                    # 检查是否可以进行电流复位判断
                    if zone_info.cutoff_time is not None:
                        elapsed = time.time() - zone_info.cutoff_time
                        if elapsed >= self._cutoff_reset_delay:
                            zone_info.can_check_reset = True
                
                await asyncio.sleep(self._poll_interval)
                
            except Exception as e:
                self._logger.error(f"轮询错误: {e}")
                await asyncio.sleep(1)
        
        self._logger.info("串口轮询任务已停止")
    
    def _request_lora_config(self):
        """请求LoRa配置 (Fire and Forget)"""
        if self._helper and self._helper.is_open:
            asyncio.create_task(self._do_request_lora())
            
    async def _do_request_lora(self):
        # 按发送顺序记录期待的响应类型
        self._lora_response_queue.append('id')
        await self._helper.get_lora_id()
        await asyncio.sleep(0.3)
        self._lora_response_queue.append('channel')
        await self._helper.get_lora_channel()
    
    def _on_data_received(self, response: SerialResponse):
        """处理串口响应 (Called from Loop)"""
        address = response.address
        function_code = response.function_code
        data = response.data
        
        self._logger.info(f"收到响应: addr={address}, func={function_code}, data={data.hex()}, raw={response.raw_data.hex()}")
        
        if function_code == 0x03:  # 读取响应
            if len(data) >= 2:
                value = (data[0] << 8) | data[1]
                
                # 检查是否有等待的LoRa响应
                if address == 0x01 and self._lora_response_queue:
                    # 按FIFO顺序取出期待的响应类型
                    expected_type = self._lora_response_queue.pop(0)
                    if expected_type == 'id':
                        self._lora_config.id = value
                        self._lora_config.last_update = time.time()
                        self._logger.info(f"LoRa编号: {value}")
                    elif expected_type == 'channel':
                        self._lora_config.channel = value
                        self._lora_config.last_update = time.time()
                        self._logger.info(f"LoRa信道: {value}")
                    
                else:
                    self._update_current(address, value)
        
        elif function_code == 0x05:
            self._logger.info(f"继电器操作成功: addr={address}")
        
        elif function_code == 0x06:
            self._logger.info(f"寄存器写入成功: addr={address}")
    
    def _update_current(self, address: int, value: int):
        """更新电流值"""
        serial_index = address - 0x01
        
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
                            # 回调可能需要在Loop外执行? 暂时直接执行
                            self._on_current_update(zone_id, value, info.is_fire_on)
                        except Exception as e:
                            self._logger.error(f"电流更新回调错误: {e}")
                    break
    
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
        # thread-safe check
        if self._helper:
            is_open = self._helper.is_open
            debug_hex = self._helper.get_debug_hex()
            
        return {
            "enabled": self._enabled,
            "port": self._port,
            "baudrate": self._baudrate,
            "poll_interval": self._poll_interval,
            "is_open": is_open,
            "debug_hex": debug_hex
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

    # ==================== 控制接口 (支持Sync/Async调用) ====================
    
    def cutoff(self, zone_id: str) -> bool:
        """执行切电 (Sync, Non-blocking)"""
        with self._lock:
            if zone_id not in self._zone_currents:
                return False
            info = self._zone_currents[zone_id]
            info.cutoff_time = time.time()
            info.can_check_reset = False
            serial_index = info.serial_index
            
        if self._loop and self._helper:
             asyncio.run_coroutine_threadsafe(
                 self._do_cutoff(serial_index, zone_id), 
                 self._loop
             )
             return True
        return False
        
    async def _do_cutoff(self, serial_index: int, zone_id: str):
        if self._helper and self._helper.is_open:
            await self._helper.set_relay(serial_index)
            self._logger.info(f"[{zone_id}] 发送切电命令")

    def set_lora_id(self, lora_id: int) -> bool:
        """设置LoRa编号 (Sync return, performs async action)"""
        if self._loop and self._helper:
            asyncio.run_coroutine_threadsafe(
                self._async_set_lora_id(lora_id),
                self._loop
            )
            return True
        return False

    async def _async_set_lora_id(self, lora_id: int):
        if self._helper and self._helper.is_open:
            await self._helper.set_lora_id(lora_id)
            await asyncio.sleep(0.3)
            # 记录期待的响应类型
            self._lora_response_queue.append('id')
            await self._helper.get_lora_id()

    def set_lora_channel(self, channel: int) -> bool:
        """设置LoRa信道"""
        if self._loop and self._helper:
            asyncio.run_coroutine_threadsafe(
                self._async_set_lora_channel(channel),
                self._loop
            )
            return True
        return False

    async def _async_set_lora_channel(self, channel: int):
        if self._helper and self._helper.is_open:
            await self._helper.set_lora_channel(channel)
            await asyncio.sleep(0.3)
            # 记录期待的响应类型
            self._lora_response_queue.append('channel')
            await self._helper.get_lora_channel()

    def update_serial_config(self, 
                             enabled: bool = None,
                             port: str = None, 
                             baudrate: int = None,
                             poll_interval: float = None) -> bool:
        """更新配置"""
        need_restart = False
        if enabled is not None:
            self._enabled = enabled
            need_restart = True
        if port is not None and port != self._port:
            self._port = port
            need_restart = True
        if baudrate is not None and baudrate != self._baudrate:
            self._baudrate = baudrate
            need_restart = True
        if poll_interval is not None:
            self._poll_interval = poll_interval
            
        if need_restart:
            self.stop()
            time.sleep(0.5)
            self.initialize(
                self._enabled,
                self._port,
                self._baudrate,
                self._poll_interval
            )
        return True

    def stop(self):
        """停止"""
        self._running = False
        if self._loop:
            self._loop.call_soon_threadsafe(self._loop.stop)
        
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=2.0)
        
        self._logger.info("串口管理器已停止")


# 全局串口管理器实例
serial_manager = SerialManager()



