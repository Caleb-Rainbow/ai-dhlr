"""
WebSocket 消息分发中心
管理本地和远程 WebSocket 连接，实现消息路由和分发
"""
import asyncio
import json
import time
from typing import Set, Dict, Any, Optional
from fastapi import WebSocket, WebSocketDisconnect

from ..utils.logger import get_logger
from ..utils.config import config_manager

logger = get_logger()


class LocalConnectionManager:
    """本地 WebSocket 连接管理器"""
    
    def __init__(self):
        self.active_connections: Set[WebSocket] = set()
        self._lock = asyncio.Lock()
    
    async def connect(self, websocket: WebSocket):
        """接受新连接"""
        await websocket.accept()
        async with self._lock:
            self.active_connections.add(websocket)
        logger.info(f"本地WebSocket客户端已连接，当前连接数: {len(self.active_connections)}")
    
    async def disconnect(self, websocket: WebSocket):
        """断开连接"""
        async with self._lock:
            self.active_connections.discard(websocket)
        logger.info(f"本地WebSocket客户端已断开，当前连接数: {len(self.active_connections)}")
    
    async def broadcast(self, message: Dict[str, Any]):
        """广播消息给所有本地客户端"""
        if not self.active_connections:
            return
        
        json_message = json.dumps(message, ensure_ascii=False)
        
        async with self._lock:
            disconnected = set()
            for connection in self.active_connections:
                try:
                    await connection.send_text(json_message)
                except Exception as e:
                    logger.warning(f"发送本地WebSocket消息失败: {e}")
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
    
    async def handle_message(self, websocket: WebSocket, message: dict) -> Optional[dict]:
        """
        处理客户端消息
        
        Args:
            websocket: 客户端连接
            message: 解析后的消息
            
        Returns:
            响应消息（如果有）
        """
        msg_type = message.get('type', '')
        
        if msg_type == 'request':
            # 请求-响应模式
            from .ws_handler import ws_handler
            response = await ws_handler.handle_request(message)
            await self.send_personal(websocket, response)
            return response
        elif msg_type == 'ping':
            # 心跳响应
            await self.send_personal(websocket, {'type': 'pong', 'timestamp': int(time.time() * 1000)})
            return None
        else:
            # 其他消息类型，记录日志
            logger.debug(f"收到未知类型消息: {msg_type}")
            return None
    
    @property
    def connection_count(self) -> int:
        return len(self.active_connections)


class MessageDispatcher:
    """
    消息分发中心
    统一管理本地和远程 WebSocket 连接，负责消息路由
    """
    
    def __init__(self):
        self.local_manager = LocalConnectionManager()
        self._remote_client = None  # 延迟导入，避免循环依赖
        self._lock = asyncio.Lock()
        self._main_loop: Optional[asyncio.AbstractEventLoop] = None  # 主事件循环引用
    
    def set_main_loop(self, loop: asyncio.AbstractEventLoop):
        """设置主事件循环引用（在 FastAPI 启动时调用）"""
        self._main_loop = loop
        logger.info(f"主事件循环已设置: {loop}")
    
    def get_main_loop(self) -> Optional[asyncio.AbstractEventLoop]:
        """
        获取主事件循环
        
        按以下优先级获取:
        1. 已保存的主事件循环引用
        2. 尝试获取当前运行的事件循环
        """
        # 优先使用保存的主事件循环
        if self._main_loop is not None:
            try:
                if not self._main_loop.is_closed():
                    return self._main_loop
            except Exception:
                pass
        
        # 尝试获取当前运行的事件循环
        try:
            loop = asyncio.get_running_loop()
            return loop
        except RuntimeError:
            pass
        
        # 尝试 get_event_loop（兼容旧版本）
        try:
            loop = asyncio.get_event_loop()
            if loop.is_running():
                return loop
        except (RuntimeError, DeprecationWarning):
            pass
        
        return None
    
    @property
    def remote_client(self):
        """获取远程客户端（延迟导入）"""
        if self._remote_client is None:
            try:
                from .websocket_client import remote_ws_client
                self._remote_client = remote_ws_client
            except ImportError:
                pass
        return self._remote_client
    
    async def connect_local(self, websocket: WebSocket):
        """接受本地连接"""
        await self.local_manager.connect(websocket)
    
    async def disconnect_local(self, websocket: WebSocket):
        """断开本地连接"""
        await self.local_manager.disconnect(websocket)
    
    async def broadcast_to_all(self, message: Dict[str, Any]):
        """广播消息到所有连接（本地+远程）"""
        # 添加时间戳
        if 'timestamp' not in message:
            message['timestamp'] = int(time.time() * 1000)
        
        # 添加设备ID
        if 'device_id' not in message:
            message['device_id'] = config_manager.config.system.device_id
        
        # 广播到本地
        await self.local_manager.broadcast(message)
        
        # 发送到远程
        if self.remote_client and self.remote_client.is_connected:
            await self.remote_client.send(message)
    
    async def broadcast_to_local(self, message: Dict[str, Any]):
        """仅广播到本地客户端"""
        await self.local_manager.broadcast(message)
    
    async def send_to_remote(self, message: Dict[str, Any]):
        """仅发送到远程服务器"""
        if self.remote_client and self.remote_client.is_connected:
            # 添加设备标识
            if 'device_id' not in message:
                message['device_id'] = config_manager.config.system.device_id
            if 'timestamp' not in message:
                message['timestamp'] = int(time.time() * 1000)
            
            await self.remote_client.send(message)
    
    async def dispatch(self, message: Dict[str, Any], source: str = "local"):
        """
        分发消息
        根据消息来源和类型决定路由
        
        Args:
            message: 消息内容
            source: 消息来源 "local" | "remote"
        """
        msg_type = message.get('type', '')
        msg_id = message.get('msg_id', '')
        target = message.get('target', 'all')  # "local" | "remote" | "all"
        
        logger.debug(f"分发消息: type={msg_type}, source={source}, target={target}")
        
        if source == "remote":
            # 来自远程服务器的消息，转发给本地客户端
            await self.local_manager.broadcast(message)
        elif source == "local":
            # 来自本地客户端的消息
            if target == "remote":
                await self.send_to_remote(message)
            elif target == "all":
                await self.broadcast_to_all(message)
            else:
                # 仅本地
                await self.local_manager.broadcast(message)
    
    def get_status(self) -> Dict[str, Any]:
        """获取连接状态"""
        remote_status = {}
        if self.remote_client:
            state = self.remote_client.state
            remote_status = {
                "is_connected": state.is_connected,
                "is_connecting": state.is_connecting,
                "last_error": state.last_error,
                "reconnect_attempts": state.reconnect_attempts
            }
        
        return {
            "local_connections": self.local_manager.connection_count,
            "remote": remote_status
        }


# 全局消息分发中心
message_dispatcher = MessageDispatcher()

# 兼容旧API
ws_manager = message_dispatcher.local_manager


async def broadcast_state_change(event_data: dict):
    """广播状态变化事件（本地+远程）"""
    message = {
        "type": "state_change",
        "data": event_data
    }
    await message_dispatcher.broadcast_to_all(message)


async def broadcast_status_update(statuses: list):
    """广播状态更新（本地+远程）"""
    message = {
        "type": "status_update",
        "data": statuses
    }
    await message_dispatcher.broadcast_to_all(message)


async def broadcast_network_status(network_status: dict):
    """广播网络状态变化"""
    message = {
        "type": "network_status",
        "data": network_status
    }
    await message_dispatcher.broadcast_to_all(message)


async def broadcast_alarm_event(zone_id: str, zone_name: str, alarm_type: str, 
                                image_base64: str = None, message: str = None):
    """
    广播报警事件（预警、报警、切电）
    
    Args:
        zone_id: 灶台ID
        zone_name: 灶台名称
        alarm_type: 报警类型 "warning" | "alarm" | "cutoff"
        image_base64: 抓拍图片Base64编码
        message: 事件消息
    """
    try:
        image_data = f"data:image/jpeg;base64,{image_base64}" if image_base64 else None
        message = {
            "type": "alarm_event",
            "data": {
                "zone_id": zone_id,
                "zone_name": zone_name,
                "alarm_type": alarm_type,
                "image": image_data,
                "message": message or f"{zone_name} 触发{_get_alarm_type_name(alarm_type)}"
            }
        }
        await message_dispatcher.broadcast_to_all(message)
    except Exception as e:
        logger.error(f"报警事件广播失败: {e}", exc_info=True)


def _get_alarm_type_name(alarm_type: str) -> str:
    """获取报警类型中文名称"""
    names = {
        "warning": "预警",
        "alarm": "报警",
        "cutoff": "切电"
    }
    return names.get(alarm_type, alarm_type)


def sync_broadcast_alarm_event(zone_id: str, zone_name: str, alarm_type: str, 
                                image_base64: str = None, message: str = None):
    """
    同步版本的报警事件广播
    用于从非异步上下文调用
    
    注意: 必须在主事件循环中发送 WebSocket 消息，因为 WebSocket 连接
    是在主事件循环中创建的。在新事件循环中操作 WebSocket 会导致问题。
    """
    try:
        # 获取主事件循环
        loop = message_dispatcher.get_main_loop()
        
        if loop is not None and loop.is_running():
            # 将协程提交到主事件循环执行
            future = asyncio.run_coroutine_threadsafe(
                broadcast_alarm_event(zone_id, zone_name, alarm_type, image_base64, message),
                loop
            )
            # 不等待结果，让它在后台执行
            # 但记录任何异常
            def handle_exception(fut):
                try:
                    fut.result()
                except Exception as e:
                    logger.error(f"广播报警事件失败: {e}")
            future.add_done_callback(handle_exception)
        else:
            # 如果没有可用的事件循环，记录警告
            logger.warning(f"无法广播报警事件: 主事件循环不可用 (zone={zone_id}, type={alarm_type})")
    except Exception as e:
        logger.error(f"同步广播报警事件失败: {e}", exc_info=True)


def sync_broadcast_state_change(event_data: dict):
    """
    同步版本的状态变化广播
    用于从非异步上下文调用
    
    注意: 必须在主事件循环中发送 WebSocket 消息，因为 WebSocket 连接
    是在主事件循环中创建的。在新事件循环中操作 WebSocket 会导致问题。
    """
    try:
        # 获取主事件循环
        loop = message_dispatcher.get_main_loop()
        
        if loop is not None and loop.is_running():
            # 将协程提交到主事件循环执行
            future = asyncio.run_coroutine_threadsafe(
                broadcast_state_change(event_data),
                loop
            )
            # 不等待结果，让它在后台执行
            # 但记录任何异常
            def handle_exception(fut):
                try:
                    fut.result()
                except Exception as e:
                    logger.error(f"广播状态变化失败: {e}")
            future.add_done_callback(handle_exception)
        else:
            # 如果没有可用的事件循环，记录警告
            zone_id = event_data.get("zone_id", "unknown")
            new_state = event_data.get("new_state", "unknown")
            logger.warning(f"无法广播状态变化: 主事件循环不可用 (zone={zone_id}, state={new_state})")
    except Exception as e:
        logger.error(f"同步广播状态变化事件失败: {e}", exc_info=True)


async def broadcast_patrol_event(event_type: str, data: dict):
    """
    广播巡检事件
    
    Args:
        event_type: 事件类型 "status_update" | "result" | "error"
        data: 事件数据
    """
    message = {
        "type": "patrol_event",
        "event_type": event_type,
        "data": data
    }
    await message_dispatcher.broadcast_to_all(message)


def sync_broadcast_patrol_event(event_type: str, data: dict):
    """
    同步版本的巡检事件广播
    用于从非异步上下文调用
    
    注意: 必须在主事件循环中发送 WebSocket 消息，因为 WebSocket 连接
    是在主事件循环中创建的。在新事件循环中操作 WebSocket 会导致问题。
    """
    try:
        # 获取主事件循环
        loop = message_dispatcher.get_main_loop()
        
        if loop is not None and loop.is_running():
            # 将协程提交到主事件循环执行
            future = asyncio.run_coroutine_threadsafe(
                broadcast_patrol_event(event_type, data),
                loop
            )
            # 不等待结果，让它在后台执行
            # 但记录任何异常
            def handle_exception(fut):
                try:
                    fut.result()
                except Exception as e:
                    logger.error(f"广播巡检事件失败: {e}")
            future.add_done_callback(handle_exception)
        else:
            # 如果没有可用的事件循环，记录警告
            logger.warning(f"无法广播巡检事件: 主事件循环不可用 (type={event_type})")
    except Exception as e:
        logger.error(f"同步广播巡检事件失败: {e}", exc_info=True)
