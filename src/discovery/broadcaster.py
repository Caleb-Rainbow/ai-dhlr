"""
局域网设备发现服务（UDP 广播）

像 IP 摄像头厂家的扫描软件一样，让局域网内的 APP / PC / 脚本能秒级发现本设备。

工作模式：
1. 被动应答（主）：客户端向 255.255.255.255:<udp_port> 广播 "DHLR_DISCOVER[/ver]"，
   本服务收到后单播回 JSON（见 build_announcement）。
2. 主动广播（辅）：每 announce_interval 秒主动广播一次自身信息，
   让被动监听的客户端（打开 APP 还没点扫描）也能立刻看到设备。

协议字段见 build_announcement()。UDP 应答与 HTTP /api/device/identify 共用同一数据源。
"""
import asyncio
import ipaddress
import json
import socket
import time
from typing import Optional, Tuple

from ..utils.config import config_manager
from ..utils.logger import get_logger

logger = get_logger()

# 协议常量
MAGIC_REQUEST = "DHLR_DISCOVER"   # 客户端请求前缀，兼容 "DHLR_DISCOVER" 与 "DHLR_DISCOVER/1"
MAGIC_RESPONSE = "DHLR_DEVICE"
PROTOCOL_VERSION = 1
DEFAULT_PORT = 32100
BROADCAST_ADDR = "255.255.255.255"


def _is_private_ip(ip: str) -> bool:
    """判断是否私有/回环地址（用于过滤公网探测）"""
    try:
        addr = ipaddress.ip_address(ip)
        return addr.is_private or addr.is_loopback
    except ValueError:
        return False


class _DiscoveryProtocol(asyncio.DatagramProtocol):
    """asyncio UDP 协议：接收发现请求并单播应答"""

    def __init__(self, service: "DiscoveryService"):
        self._service = service
        self.transport: Optional[asyncio.DatagramTransport] = None

    def connection_made(self, transport: asyncio.DatagramTransport) -> None:
        self.transport = transport

    def datagram_received(self, data: bytes, addr: Tuple[str, int]) -> None:
        try:
            text = data.decode("utf-8", errors="ignore").strip()
            # 校验请求 magic 前缀
            if not text.startswith(MAGIC_REQUEST):
                return
            src_ip = addr[0]
            # 可选：仅应答私有网段来源，防公网/跨网段探测
            if self._service.require_private_source and not _is_private_ip(src_ip):
                return
            payload = self._service.get_announcement()
            raw = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            self.transport.sendto(raw, addr)
        except Exception as e:
            logger.warning(f"处理发现请求失败 ({addr}): {e}")


class DiscoveryService:
    """设备发现服务（单例）"""

    def __init__(self):
        self._enabled = True
        self._port = DEFAULT_PORT
        self._interval = 15
        self._model = "DHLR-RK3568"
        self.require_private_source = True

        self._transport: Optional[asyncio.DatagramTransport] = None
        self._announce_task: Optional[asyncio.Task] = None
        self._running = False
        self._start_time = time.time()

    @property
    def running(self) -> bool:
        return self._running

    async def start(self) -> None:
        """启动发现服务（在 FastAPI lifespan 中调用）。

        防御式：端口占用等异常只告警，绝不阻断主服务。
        """
        # 读取配置（配置缺失时用默认值）
        try:
            cfg = config_manager.config.discovery
            self._enabled = bool(cfg.enabled)
            self._port = int(cfg.udp_port)
            self._interval = int(cfg.announce_interval)
            self._model = cfg.model or self._model
            self.require_private_source = bool(cfg.require_private_source)
        except Exception as e:
            logger.warning(f"读取 discovery 配置失败，使用默认值: {e}")

        if not self._enabled:
            logger.info("设备发现服务已禁用 (discovery.enabled=false)")
            return

        loop = asyncio.get_running_loop()

        # 1) 监听被动发现请求
        try:
            self._transport, _ = await loop.create_datagram_endpoint(
                lambda: _DiscoveryProtocol(self),
                local_addr=("0.0.0.0", self._port),
                allow_broadcast=True,
            )
            logger.info(f"设备发现服务监听 UDP 0.0.0.0:{self._port}（被动应答就绪）")
        except OSError as e:
            logger.warning(
                f"设备发现服务监听 UDP {self._port} 失败（被动应答不可用）: {e}"
            )
            self._transport = None

        # 2) 主动广播
        if self._interval > 0:
            self._announce_task = asyncio.create_task(self._announce_loop())

        self._running = True

    async def _announce_loop(self) -> None:
        """周期性主动广播。首次立即广播一次，让被动监听端尽快发现。"""
        await self._broadcast_once()
        while True:
            try:
                await asyncio.sleep(self._interval)
                await self._broadcast_once()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.warning(f"主动广播异常: {e}")
                await asyncio.sleep(self._interval)

    async def _broadcast_once(self) -> None:
        """发一次主动广播到受限广播地址 255.255.255.255"""
        raw = json.dumps(self.get_announcement(), ensure_ascii=False).encode("utf-8")
        sock = None
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
            sock.settimeout(0.2)
            sock.sendto(raw, (BROADCAST_ADDR, self._port))
        except Exception as e:
            logger.debug(f"主动广播发送失败: {e}")
        finally:
            if sock is not None:
                try:
                    sock.close()
                except Exception:
                    pass

    def get_announcement(self) -> dict:
        """组装设备信息字典（UDP 应答与 HTTP 端点共用，单一数据源）"""
        ip, iface, mac = self._get_network_info()
        cfg = config_manager.config
        system = cfg.system
        api_port = cfg.api.port

        online = True
        try:
            from ..utils.network_monitor import network_monitor
            ns = network_monitor.status
            online = bool(ns.is_connected)
        except Exception:
            pass

        return {
            "magic": MAGIC_RESPONSE,
            "ver": PROTOCOL_VERSION,
            "device_id": system.device_id or "dhlr",
            "name": system.name,
            "model": self._model,
            "ip": ip or "0.0.0.0",
            "interface": iface,
            "api_port": api_port,
            "ws_port": api_port,   # WebSocket 与 API 同端口
            "firmware": system.version,
            "mac": mac,
            "online": online,
            "uptime_s": int(time.time() - self._start_time),
            "ts": int(time.time()),
        }

    @staticmethod
    def _get_network_info() -> Tuple[str, str, str]:
        """获取本机 IP / 接口名 / MAC。

        优先复用 network_monitor 已采集的 IP/接口；MAC 用 psutil（项目已有依赖）。
        IP 兜底：用 UDP connect 取默认路由出口 IP。
        """
        ip, iface, mac = "", "", ""

        # IP / 接口名：复用 network_monitor 单例
        try:
            from ..utils.network_monitor import network_monitor
            ns = network_monitor.status
            ip = ns.ip_address or ""
            iface = ns.interface_name or ""
        except Exception:
            pass

        # IP 兜底
        if not ip:
            sock = None
            try:
                sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
                sock.settimeout(0.2)
                sock.connect(("8.8.8.8", 80))
                ip = sock.getsockname()[0]
            except Exception:
                ip = ""
            finally:
                if sock is not None:
                    try:
                        sock.close()
                    except Exception:
                        pass

        # MAC：psutil 按接口名匹配（无接口名则取首个有效 MAC）
        try:
            import psutil
            addrs = psutil.net_if_addrs()
            candidates = [iface] if iface else list(addrs.keys())
            for name in candidates:
                for a in addrs.get(name, []):
                    if a.family == psutil.AF_LINK and a.address and a.address != "00:00:00:00:00:00":
                        mac = a.address
                        if not iface:
                            iface = name
                        break
                if mac:
                    break
        except Exception:
            pass

        return ip, iface, mac

    async def stop(self) -> None:
        """停止发现服务"""
        if self._announce_task:
            self._announce_task.cancel()
            try:
                await self._announce_task
            except asyncio.CancelledError:
                pass
            self._announce_task = None
        if self._transport:
            try:
                self._transport.close()
            except Exception:
                pass
            self._transport = None
        self._running = False
        logger.info("设备发现服务已停止")


# 全局单例
discovery_service = DiscoveryService()
