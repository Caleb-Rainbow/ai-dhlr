"""局域网设备发现服务（UDP 广播）

对外导出单例 discovery_service，供 server.py 在 FastAPI lifespan 中启停。
"""
from .broadcaster import discovery_service, DiscoveryService

__all__ = ["discovery_service", "DiscoveryService"]
