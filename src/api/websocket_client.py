"""
远程 WebSocket 客户端
负责与远程服务器建立 WebSocket 连接，支持 Token 鉴权、心跳机制和自动重连
"""
import asyncio
import json
import time
import aiohttp
from typing import Optional, Callable, Dict, Any, List, Awaitable
from dataclasses import dataclass
from urllib.parse import urlparse, urljoin

from ..utils.logger import get_logger
from ..utils.config import config_manager

logger = get_logger()


@dataclass
class RemoteConnectionState:
    """远程连接状态"""
    is_connected: bool = False
    is_connecting: bool = False
    last_error: str = ""
    reconnect_attempts: int = 0
    last_heartbeat: float = 0


class RemoteWebSocketClient:
    """远程 WebSocket 客户端"""
    
    def __init__(self):
        self._ws: Optional[aiohttp.ClientWebSocketResponse] = None
        self._session: Optional[aiohttp.ClientSession] = None
        self._state = RemoteConnectionState()
        self._running = False
        self._heartbeat_task: Optional[asyncio.Task] = None
        self._receive_task: Optional[asyncio.Task] = None
        self._reconnect_task: Optional[asyncio.Task] = None
        self._message_handlers: List[Callable[[dict], Awaitable[None]]] = []
        self._heartbeat_interval = 10.0  # 心跳间隔（秒）
        self._max_reconnect_delay = 30.0  # 最大重连延迟（秒）
    
    @property
    def state(self) -> RemoteConnectionState:
        return self._state
    
    @property
    def is_connected(self) -> bool:
        return self._state.is_connected and self._ws is not None and not self._ws.closed
    
    async def add_message_handler(self, handler: Callable[[dict], Awaitable[None]]):
        """添加异步消息处理器"""
        self._message_handlers.append(handler)

    async def remove_message_handler(self, handler: Callable[[dict], Awaitable[None]]):
        """移除消息处理器"""
        if handler in self._message_handlers:
            self._message_handlers.remove(handler)
    
    def _build_urls(self) -> tuple:
        """
        根据服务器地址构建登录 URL 和 WebSocket URL
        返回 (login_url, ws_url)
        """
        config = config_manager.config.remote
        server_url = config.server_url.strip()
        
        if not server_url:
            return ("", "")
        
        # 确保有协议前缀
        if not server_url.startswith(('http://', 'https://')):
            server_url = 'http://' + server_url
        
        parsed = urlparse(server_url)
        
        # 构建登录 URL
        login_path = config.login_path.strip()
        if not login_path.startswith('/'):
            login_path = '/' + login_path
        login_url = f"{parsed.scheme}://{parsed.netloc}{login_path}"
        
        # 构建 WebSocket URL
        ws_scheme = 'wss' if parsed.scheme == 'https' else 'ws'
        ws_path = config.websocket_path.strip()
        if not ws_path.startswith('/'):
            ws_path = '/' + ws_path
        ws_url = f"{ws_scheme}://{parsed.netloc}{ws_path}"
        
        return (login_url, ws_url)
    
    async def login(self) -> tuple:
        """
        调用登录接口获取 Token
        返回 (success, token, error_message)
        """
        config = config_manager.config.remote
        login_url, _ = self._build_urls()
        
        if not login_url:
            return (False, "", "服务器地址未配置")
        
        if not config.username or not config.password:
            return (False, "", "用户名或密码未配置")
        
        try:
            timeout = aiohttp.ClientTimeout(total=10)
            async with aiohttp.ClientSession(timeout=timeout) as session:
                payload = {
                    "username": config.username,
                    "password": config.password
                }
                
                async with session.post(login_url, json=payload) as response:
                    if response.status == 200:
                        data = await response.json()
                        if data.get('code') == 200:
                            token = data.get('token', '')
                            if token:
                                # 保存 Token 到配置
                                config.token = token
                                config.token_expires = int(time.time()) + 3600 * 24  # 假设24小时有效
                                config_manager.save()
                                logger.info("远程服务器登录成功")
                                return (True, token, "")
                            else:
                                return (False, "", "响应中无 Token")
                        else:
                            msg = data.get('msg', '登录失败')
                            return (False, "", msg)
                    elif response.status == 401:
                        return (False, "", "用户名或密码错误")
                    else:
                        text = await response.text()
                        return (False, "", f"HTTP {response.status}: {text[:100]}")
                        
        except asyncio.TimeoutError:
            return (False, "", "连接超时")
        except aiohttp.ClientError as e:
            return (False, "", f"网络错误: {str(e)}")
        except Exception as e:
            return (False, "", f"登录失败: {str(e)}")
    
    async def connect(self) -> bool:
        """建立 WebSocket 连接"""
        if self._state.is_connecting:
            return False
        
        self._state.is_connecting = True
        self._state.last_error = ""
        
        try:
            config = config_manager.config.remote
            _, ws_url = self._build_urls()
            
            if not ws_url:
                self._state.last_error = "WebSocket 地址无效"
                return False
            
            # 检查 Token
            if not config.token:
                logger.info("Token 不存在，尝试登录...")
                success, token, error = await self.login()
                if not success:
                    self._state.last_error = error
                    return False
            
            # 创建 session
            if self._session is None or self._session.closed:
                self._session = aiohttp.ClientSession()
            
            # 建立 WebSocket 连接
            headers = {
                "Authorization": f"Bearer {config.token}"
            }
            
            logger.info(f"正在连接远程服务器: {ws_url}")
            self._ws = await self._session.ws_connect(
                ws_url,
                headers=headers,
                heartbeat=self._heartbeat_interval,
                receive_timeout=30
            )
            
            self._state.is_connected = True
            self._state.is_connecting = False
            self._state.reconnect_attempts = 0
            self._state.last_heartbeat = time.time()

            logger.info("远程 WebSocket 连接成功")

            # 启动心跳和接收任务
            self._heartbeat_task = asyncio.create_task(self._heartbeat_loop())
            self._receive_task = asyncio.create_task(self._receive_loop())

            # 补发离线缓存的消息
            asyncio.create_task(self._resend_cached_messages())
            
            return True
            
        except aiohttp.WSServerHandshakeError as e:
            if e.status == 401:
                self._state.last_error = "Token 无效或已过期"
                logger.warning("Token 无效，尝试重新登录...")
                # 清除旧 Token 并重新登录
                config_manager.config.remote.token = ""
                config_manager.save()
            else:
                self._state.last_error = f"握手失败: {e.status}"
            return False
        except Exception as e:
            self._state.last_error = f"连接失败: {str(e)}"
            logger.error(f"远程 WebSocket 连接失败: {e}")
            return False
        finally:
            self._state.is_connecting = False
    
    async def disconnect(self):
        """断开连接"""
        self._running = False
        
        # 取消任务
        for task in [self._heartbeat_task, self._receive_task, self._reconnect_task]:
            if task:
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass
        
        self._heartbeat_task = None
        self._receive_task = None
        self._reconnect_task = None
        
        # 关闭 WebSocket
        if self._ws and not self._ws.closed:
            await self._ws.close()
        self._ws = None
        
        # 关闭 session
        if self._session and not self._session.closed:
            await self._session.close()
        self._session = None
        
        self._state.is_connected = False
        logger.info("远程 WebSocket 已断开")
    
    async def send(self, message: dict) -> bool:
        """发送消息"""
        if not self.is_connected:
            return False
        
        try:
            json_str = json.dumps(message, ensure_ascii=False)
            await self._ws.send_str(json_str)
            return True
        except Exception as e:
            logger.error(f"发送远程消息失败: {e}")
            return False
    
    async def _heartbeat_loop(self):
        """心跳循环"""
        while self._running and self.is_connected:
            try:
                # 发送心跳包（按照协议规范）
                ping = {
                    "type": "ping"
                }

                await self.send(ping)
                self._state.last_heartbeat = time.time()
                
                await asyncio.sleep(self._heartbeat_interval)
                
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"心跳发送失败: {e}")
                break
    
    async def _receive_loop(self):
        """接收消息循环"""
        while self._running and self.is_connected:
            try:
                msg = await self._ws.receive()
                
                if msg.type == aiohttp.WSMsgType.TEXT:
                    try:
                        data = json.loads(msg.data)
                        await self._handle_message(data)
                    except json.JSONDecodeError:
                        logger.warning(f"收到非JSON消息: {msg.data[:100]}")
                        
                elif msg.type == aiohttp.WSMsgType.CLOSED:
                    logger.warning("远程 WebSocket 连接已关闭")
                    break
                    
                elif msg.type == aiohttp.WSMsgType.ERROR:
                    logger.error(f"远程 WebSocket 错误: {self._ws.exception()}")
                    break
                    
            except asyncio.CancelledError:
                break
            except asyncio.TimeoutError:
                continue
            except Exception as e:
                logger.error(f"接收远程消息失败: {e}")
                break
        
        # 连接断开，触发重连
        self._state.is_connected = False
        if self._running:
            asyncio.create_task(self._reconnect())
    
    async def _handle_message(self, message: dict):
        """处理收到的消息"""
        msg_type = message.get('type', '')

        # 处理心跳响应
        if msg_type == 'pong':
            self._state.last_heartbeat = time.time()
            return

        # 处理报警记录确认
        if msg_type == 'alarm_record_ack':
            await self._handle_alarm_ack(message)
            return

        # 处理 401 错误（Token 失效）
        if msg_type == 'error' and message.get('code') == 401:
            logger.warning("收到 401 错误，Token 可能已失效")
            self._state.last_error = "Token 已失效"
            # 清除 Token 并重连
            config_manager.config.remote.token = ""
            config_manager.save()
            asyncio.create_task(self._reconnect())
            return

        # 通知所有消息处理器
        for handler in self._message_handlers:
            try:
                await handler(message)
            except Exception as e:
                logger.error(f"消息处理器执行失败: {e}")

    async def _handle_alarm_ack(self, data: dict):
        """处理报警记录确认"""
        msg_id = data.get('msg_id')
        success = data.get('success', False)

        if success:
            record_id = data.get('record_id')
            logger.info(f"报警记录上传成功: msg_id={msg_id}, record_id={record_id}")
        else:
            error = data.get('error', '未知错误')
            logger.warning(f"报警记录上传失败: msg_id={msg_id}, error={error}")

    async def _reconnect(self):
        """重连逻辑（指数退避）"""
        if not self._running:
            return

        self._state.reconnect_attempts += 1

        # 计算延迟（指数退避：1, 2, 4, 8, 16, 30...）
        delay = min(2 ** (self._state.reconnect_attempts - 1), self._max_reconnect_delay)

        logger.info(f"将在 {delay} 秒后尝试第 {self._state.reconnect_attempts} 次重连...")

        await asyncio.sleep(delay)

        if not self._running:
            return

        # 尝试重连
        success = await self.connect()
        if not success and self._running:
            # 继续重连
            asyncio.create_task(self._reconnect())

    async def _resend_cached_messages(self):
        """补发离线缓存的消息"""
        try:
            from .offline_cache import offline_cache

            if offline_cache.is_empty:
                return

            cached_messages = offline_cache.peek_all()
            if not cached_messages:
                return

            logger.info(f"开始补发 {len(cached_messages)} 条缓存消息")

            failed_messages = []
            for msg in cached_messages:
                try:
                    success = await self.send(msg)
                    if success:
                        # 成功发送后从缓存中移除
                        offline_cache.pop_all()  # 由于队列特性，需要全部取出后重新放入失败的
                    else:
                        failed_messages.append(msg)
                    await asyncio.sleep(0.1)  # 避免发送过快
                except Exception as e:
                    logger.error(f"补发消息失败: {e}")
                    failed_messages.append(msg)

            # 清空已发送的，放回失败的
            offline_cache.clear()
            if failed_messages:
                offline_cache.push_back(failed_messages)
                logger.warning(f"补发完成，{len(failed_messages)} 条消息失败已重新缓存")
            else:
                logger.info("缓存消息补发完成")

        except Exception as e:
            logger.error(f"补发缓存消息时发生错误: {e}")
    
    async def start(self):
        """启动客户端"""
        config = config_manager.config.remote
        
        if not config.enabled:
            logger.info("远程连接未启用")
            return
        
        if not config.server_url:
            logger.warning("远程服务器地址未配置")
            return
        
        self._running = True
        
        success = await self.connect()
        if not success:
            logger.warning(f"初始连接失败: {self._state.last_error}")
            # 启动重连
            asyncio.create_task(self._reconnect())
    
    async def stop(self):
        """停止客户端"""
        self._running = False
        await self.disconnect()


# 全局远程 WebSocket 客户端实例
remote_ws_client = RemoteWebSocketClient()
