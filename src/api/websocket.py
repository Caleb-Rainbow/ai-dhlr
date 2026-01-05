"""
WebSocket状态推送
实时推送灶台状态变化给客户端
"""
import asyncio
import json
from typing import Set, Dict, Any
from fastapi import WebSocket, WebSocketDisconnect

from ..utils.logger import get_logger

logger = get_logger()


class ConnectionManager:
    """WebSocket连接管理器"""
    
    def __init__(self):
        self.active_connections: Set[WebSocket] = set()
        self._lock = asyncio.Lock()
    
    async def connect(self, websocket: WebSocket):
        """接受新连接"""
        await websocket.accept()
        async with self._lock:
            self.active_connections.add(websocket)
        logger.info(f"WebSocket客户端已连接，当前连接数: {len(self.active_connections)}")
    
    async def disconnect(self, websocket: WebSocket):
        """断开连接"""
        async with self._lock:
            self.active_connections.discard(websocket)
        logger.info(f"WebSocket客户端已断开，当前连接数: {len(self.active_connections)}")
    
    async def broadcast(self, message: Dict[str, Any]):
        """广播消息给所有客户端"""
        if not self.active_connections:
            return
        
        json_message = json.dumps(message, ensure_ascii=False)
        
        async with self._lock:
            disconnected = set()
            for connection in self.active_connections:
                try:
                    await connection.send_text(json_message)
                except Exception as e:
                    logger.warning(f"发送WebSocket消息失败: {e}")
                    disconnected.add(connection)
            
            # 移除断开的连接
            self.active_connections -= disconnected
    
    async def send_personal(self, websocket: WebSocket, message: Dict[str, Any]):
        """发送消息给指定客户端"""
        try:
            json_message = json.dumps(message, ensure_ascii=False)
            await websocket.send_text(json_message)
        except Exception as e:
            logger.error(f"发送个人消息失败: {e}")


# 全局连接管理器
ws_manager = ConnectionManager()


async def broadcast_state_change(event_data: dict):
    """广播状态变化事件"""
    message = {
        "type": "state_change",
        "data": event_data
    }
    await ws_manager.broadcast(message)


async def broadcast_status_update(statuses: list):
    """广播状态更新"""
    message = {
        "type": "status_update",
        "data": statuses
    }
    await ws_manager.broadcast(message)


def sync_broadcast_state_change(event_data: dict):
    """
    同步版本的状态变化广播
    用于从非异步上下文调用
    """
    try:
        loop = asyncio.get_event_loop()
        if loop.is_running():
            asyncio.create_task(broadcast_state_change(event_data))
        else:
            loop.run_until_complete(broadcast_state_change(event_data))
    except RuntimeError:
        # 没有事件循环时忽略
        pass
