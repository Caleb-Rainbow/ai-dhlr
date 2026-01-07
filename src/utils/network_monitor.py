"""
网络状态监测模块
监测当前网络接口类型、IP地址、WiFi信号强度等
"""
import asyncio
import platform
import socket
import subprocess
import re
from dataclasses import dataclass, asdict
from typing import Optional, Callable, List

from .logger import get_logger

logger = get_logger()


@dataclass
class NetworkStatus:
    """网络状态信息"""
    interface_type: str = "unknown"  # "wifi" | "ethernet" | "unknown"
    interface_name: str = ""         # 接口名称，如 eth0, wlan0
    ip_address: str = ""             # IP 地址
    signal_strength: int = -1        # WiFi 信号强度 (0-100)，以太网为 -1
    gateway: str = ""                # 网关地址
    is_connected: bool = False       # 是否联网
    
    def to_dict(self) -> dict:
        return asdict(self)


class NetworkMonitor:
    """网络状态监测器"""
    
    _instance: Optional['NetworkMonitor'] = None
    
    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._initialized = False
        return cls._instance
    
    def __init__(self):
        if self._initialized:
            return
        self._initialized = True
        self._status = NetworkStatus()
        self._running = False
        self._monitor_task: Optional[asyncio.Task] = None
        self._callbacks: List[Callable[[NetworkStatus], None]] = []
        self._is_linux = platform.system() == "Linux"
    
    @property
    def status(self) -> NetworkStatus:
        """获取当前网络状态"""
        return self._status
    
    def add_callback(self, callback: Callable[[NetworkStatus], None]):
        """添加状态变化回调"""
        self._callbacks.append(callback)
    
    def remove_callback(self, callback: Callable[[NetworkStatus], None]):
        """移除状态变化回调"""
        if callback in self._callbacks:
            self._callbacks.remove(callback)
    
    def get_local_ip(self) -> str:
        """获取本机IP地址"""
        try:
            # 创建UDP socket获取本机IP
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.settimeout(0.1)
            try:
                # 不需要真正连接，只是获取路由信息
                s.connect(("8.8.8.8", 80))
                ip = s.getsockname()[0]
            except Exception:
                ip = "127.0.0.1"
            finally:
                s.close()
            return ip
        except Exception as e:
            logger.warning(f"获取本机IP失败: {e}")
            return "127.0.0.1"
    
    def _get_default_gateway_linux(self) -> tuple:
        """
        Linux: 通过读取 /proc/net/route 获取默认网关和接口
        返回 (interface_name, gateway_ip)
        """
        try:
            with open('/proc/net/route', 'r') as f:
                lines = f.readlines()
            
            for line in lines[1:]:  # 跳过标题行
                parts = line.strip().split()
                if len(parts) >= 3:
                    iface = parts[0]
                    dest = parts[1]
                    gateway = parts[2]
                    
                    # Destination 00000000 表示默认路由
                    if dest == "00000000":
                        # 网关地址是小端序的十六进制
                        gw_hex = gateway
                        gw_bytes = bytes.fromhex(gw_hex)
                        gw_ip = ".".join(str(b) for b in reversed(gw_bytes))
                        return (iface, gw_ip)
            
            return ("", "")
        except Exception as e:
            logger.warning(f"读取默认网关失败: {e}")
            return ("", "")
    
    def _get_interface_type(self, iface: str) -> str:
        """根据接口名称判断类型"""
        if not iface:
            return "unknown"
        
        iface_lower = iface.lower()
        
        # WiFi 接口常见名称
        if any(prefix in iface_lower for prefix in ['wlan', 'wlp', 'wifi', 'wl']):
            return "wifi"
        
        # 以太网接口常见名称
        if any(prefix in iface_lower for prefix in ['eth', 'enp', 'eno', 'ens', 'em']):
            return "ethernet"
        
        return "unknown"
    
    def _get_wifi_signal_linux(self, iface: str) -> int:
        """
        Linux: 通过 nmcli 获取 WiFi 信号强度
        返回 0-100 的信号强度，失败返回 -1
        """
        try:
            result = subprocess.run(
                ['nmcli', '-f', 'IN-USE,SIGNAL,SSID', 'device', 'wifi'],
                capture_output=True,
                text=True,
                timeout=5
            )
            
            if result.returncode != 0:
                return -1
            
            for line in result.stdout.strip().split('\n'):
                if line.startswith('*'):
                    # 当前连接的网络
                    match = re.search(r'\*\s+(\d+)', line)
                    if match:
                        return int(match.group(1))
            
            return -1
        except Exception as e:
            logger.debug(f"获取WiFi信号强度失败: {e}")
            return -1
    
    def _get_windows_network_info(self) -> tuple:
        """
        Windows: 获取网络信息（模拟数据，用于开发测试）
        返回 (interface_type, interface_name, gateway)
        """
        # Windows 下返回模拟数据
        ip = self.get_local_ip()
        if ip.startswith("192.168.") or ip.startswith("10.") or ip.startswith("172."):
            return ("ethernet", "本地连接", "192.168.1.1")
        return ("unknown", "", "")
    
    def update_status(self) -> NetworkStatus:
        """更新并返回当前网络状态"""
        old_status = NetworkStatus(
            interface_type=self._status.interface_type,
            interface_name=self._status.interface_name,
            ip_address=self._status.ip_address,
            signal_strength=self._status.signal_strength,
            gateway=self._status.gateway,
            is_connected=self._status.is_connected
        )
        
        ip = self.get_local_ip()
        is_connected = ip != "127.0.0.1"
        
        if self._is_linux:
            iface, gateway = self._get_default_gateway_linux()
            iface_type = self._get_interface_type(iface)
            signal = self._get_wifi_signal_linux(iface) if iface_type == "wifi" else -1
        else:
            iface_type, iface, gateway = self._get_windows_network_info()
            signal = -1
        
        self._status = NetworkStatus(
            interface_type=iface_type,
            interface_name=iface,
            ip_address=ip,
            signal_strength=signal,
            gateway=gateway,
            is_connected=is_connected
        )
        
        # 检查状态是否变化
        if (old_status.ip_address != self._status.ip_address or 
            old_status.interface_type != self._status.interface_type or
            old_status.is_connected != self._status.is_connected):
            self._notify_callbacks()
        
        return self._status
    
    def _notify_callbacks(self):
        """通知所有回调"""
        for callback in self._callbacks:
            try:
                callback(self._status)
            except Exception as e:
                logger.error(f"网络状态回调执行失败: {e}")
    
    async def _monitor_loop(self, interval: float = 10.0):
        """监测循环，定期检查网络状态"""
        while self._running:
            try:
                self.update_status()
                await asyncio.sleep(interval)
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"网络监测错误: {e}")
                await asyncio.sleep(interval)
    
    def start(self, interval: float = 10.0):
        """启动网络监测"""
        if self._running:
            return
        
        self._running = True
        # 立即获取一次状态
        self.update_status()
        
        # 创建异步监测任务
        try:
            loop = asyncio.get_event_loop()
            if loop.is_running():
                self._monitor_task = asyncio.create_task(self._monitor_loop(interval))
            else:
                logger.warning("事件循环未运行，网络监测任务将在事件循环启动后创建")
        except RuntimeError:
            logger.warning("无法获取事件循环，网络监测任务将在事件循环启动后创建")
    
    async def start_async(self, interval: float = 10.0):
        """异步启动网络监测"""
        if self._running:
            return
        
        self._running = True
        self.update_status()
        self._monitor_task = asyncio.create_task(self._monitor_loop(interval))
        logger.info(f"网络监测已启动，间隔: {interval}秒")
    
    async def stop(self):
        """停止网络监测"""
        self._running = False
        if self._monitor_task:
            self._monitor_task.cancel()
            try:
                await self._monitor_task
            except asyncio.CancelledError:
                pass
            self._monitor_task = None
        logger.info("网络监测已停止")


# 全局网络监测器实例
network_monitor = NetworkMonitor()
