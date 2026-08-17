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
from ..utils.config import config_manager, normalize_remote_websocket_path

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

    _MAX_AUTH_RETRIES = 5  # 鉴权失败最大重试次数

    _AUTH_FAIL_WINDOW = 5.0  # 连接建立后此时间内断开视为鉴权失败（秒）

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
        self._auth_fail_count = 0  # 连续鉴权失败计数
        self._auth_stopped = False  # 鉴权失败已停止重连
        self._connected_at: float = 0  # 连接建立时间戳
    
    @property
    def state(self) -> RemoteConnectionState:
        return self._state
    
    @property
    def is_connected(self) -> bool:
        return self._state.is_connected and self._ws is not None and not self._ws.closed
    
    async def add_message_handler(self, handler: Callable[[dict], Awaitable[None]]):
        """添加异步消息处理器（重复注册只保留一份，避免请求被处理两次）"""
        if handler not in self._message_handlers:
            self._message_handlers.append(handler)

    async def remove_message_handler(self, handler: Callable[[dict], Awaitable[None]]):
        """移除消息处理器"""
        if handler in self._message_handlers:
            self._message_handlers.remove(handler)
    
    def _build_urls(self) -> tuple:
        """
        根据服务器地址构建登录 URL 和 WebSocket URL
        返回 (login_url, ws_url)

        WebSocket URL 格式: ws://host/websocket/ws/dhlr/device/{deviceId}?token={token}
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
        # 对外路径格式: /websocket/ws/dhlr/device/{deviceId}?token={token}
        ws_scheme = 'wss' if parsed.scheme == 'https' else 'ws'
        ws_path = normalize_remote_websocket_path(config.websocket_path)
        config.websocket_path = ws_path
        if not ws_path.startswith('/'):
            ws_path = '/' + ws_path

        # 确保路径以 / 结尾，然后追加 deviceId
        # 例如: /websocket/ws/dhlr/device/ -> /websocket/ws/dhlr/device/{deviceId}
        ws_base = ws_path.rstrip('/')

        # 获取设备ID（从全局配置）
        device_id = config_manager.config.system.device_id

        # 完整的 WebSocket URL（token 在连接时通过 query string 传递）
        ws_url = f"{ws_scheme}://{parsed.netloc}{ws_base}/{device_id}"

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
                                # 尝试从响应中获取过期时间，否则默认 24 小时
                                expires_in = data.get('expires_in') or data.get('expireTime')
                                if expires_in:
                                    # 如果是毫秒或秒数
                                    if expires_in > 10000000000:  # 毫秒时间戳
                                        config.token_expires = expires_in // 1000
                                    elif expires_in > 1000000000:  # 秒时间戳
                                        config.token_expires = expires_in
                                    else:  # 相对秒数
                                        config.token_expires = int(time.time()) + expires_in
                                else:
                                    config.token_expires = int(time.time()) + 3600 * 24  # 默认 24 小时
                                config_manager.save()
                                logger.info(f"远程服务器登录成功，Token 有效期至: {config.token_expires}")
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
            
            # 检查 Token 是否存在或已过期
            need_login = False
            if not config.token:
                logger.info("Token 不存在，尝试登录...")
                need_login = True
            elif config.token_expires > 0 and time.time() >= config.token_expires:
                logger.info("Token 已过期，尝试重新登录...")
                config.token = ""  # 清除过期 Token
                need_login = True

            if need_login:
                success, token, error = await self.login()
                if not success:
                    self._auth_fail_count += 1
                    self._state.last_error = error
                    if self._auth_fail_count >= self._MAX_AUTH_RETRIES:
                        self._auth_stopped = True
                        logger.error(
                            f"鉴权连续失败 {self._auth_fail_count} 次，停止重连: {error}"
                        )
                    return False
            
            # 构建 WebSocket URL（包含 token 参数）
            # 网关对外地址: /websocket/ws/dhlr/device/{deviceId}?token={jwt_token}
            ws_url_with_token = f"{ws_url}?token={config.token}"

            # 创建 session
            if self._session is None or self._session.closed:
                self._session = aiohttp.ClientSession()

            logger.info(f"正在连接远程服务器: {ws_url}")

            self._ws = await self._session.ws_connect(
                ws_url_with_token,
                heartbeat=self._heartbeat_interval,
                receive_timeout=30
            )

            # 检查响应头中的鉴权错误标记
            # Java 服务端 modifyHandshake 在 Token 无效时设置 X-Auth-Error
            # 但不会阻止 101 升级，所以需要主动检测
            # aiohttp 的 ClientWebSocketResponse 通过 _response 访问原始响应头
            auth_error = None
            try:
                resp = getattr(self._ws, '_response', None)
                if resp and hasattr(resp, 'headers'):
                    auth_error = resp.headers.get('X-Auth-Error')
            except Exception:
                pass
            if auth_error:
                logger.warning(f"WebSocket 握手返回鉴权错误: {auth_error}")
                await self._ws.close()
                self._handle_auth_failure(f"握手鉴权失败: {auth_error}")
                return False

            self._state.is_connected = True
            self._state.is_connecting = False
            self._state.reconnect_attempts = 0
            self._state.last_heartbeat = time.time()
            self._connected_at = time.time()
            self._auth_fail_count = 0
            self._auth_stopped = False

            logger.info("远程 WebSocket 连接成功")

            # 取消旧任务，避免多个 receive loop 并发竞争
            for task in [self._heartbeat_task, self._receive_task]:
                if task and not task.done():
                    task.cancel()
            self._heartbeat_task = None
            self._receive_task = None

            # 启动心跳和接收任务
            self._heartbeat_task = asyncio.create_task(self._heartbeat_loop())
            self._receive_task = asyncio.create_task(self._receive_loop())

            # 补发离线缓存的消息
            asyncio.create_task(self._resend_cached_messages())

            return True
            
        except aiohttp.WSServerHandshakeError as e:
            if e.status == 401:
                self._auth_fail_count += 1
                self._state.last_error = "Token 无效或已过期"
                if self._auth_fail_count >= self._MAX_AUTH_RETRIES:
                    self._auth_stopped = True
                    logger.error(
                        f"鉴权连续失败 {self._auth_fail_count} 次，停止重连"
                    )
                else:
                    logger.warning(
                        f"Token 无效，清除后等待重连 ({self._auth_fail_count}/{self._MAX_AUTH_RETRIES})"
                    )
                    config_manager.config.remote.token = ""
                    config_manager.config.remote.token_expires = 0
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
                    close_code = self._ws.close_code if self._ws else None
                    elapsed = time.time() - self._connected_at if self._connected_at else 0
                    logger.warning(
                        f"远程 WebSocket 连接已关闭, code={close_code}, "
                        f"存活={elapsed:.1f}s"
                    )

                    # 连接建立后短时间内被关闭，视为鉴权失败
                    if self._connected_at and elapsed < self._AUTH_FAIL_WINDOW:
                        self._handle_auth_failure(
                            f"连接被立即关闭(close_code={close_code})，疑似Token无效"
                        )
                    break

                elif msg.type == aiohttp.WSMsgType.ERROR:
                    elapsed = time.time() - self._connected_at if self._connected_at else 0
                    logger.error(
                        f"远程 WebSocket 错误: {self._ws.exception()}, "
                        f"存活={elapsed:.1f}s"
                    )
                    if self._connected_at and elapsed < self._AUTH_FAIL_WINDOW:
                        self._handle_auth_failure("连接立即出错，疑似Token无效")
                    break

            except asyncio.CancelledError:
                # 被 connect() 主动取消（新连接接管）或 disconnect() 取消，
                # 都不是真正的断线：不要修改连接状态，也不要触发重连，
                # 否则每次重连都会被取消的旧 receive loop 繁殖出新重连链，
                # 形成自我维持的重连风暴。
                return
            except asyncio.TimeoutError:
                continue
            except Exception as e:
                logger.error(f"接收远程消息失败: {e}")
                break

        # 连接真正断开，触发重连
        self._state.is_connected = False
        if self._running:
            self._schedule_reconnect()

    def _handle_auth_failure(self, reason: str):
        """处理鉴权失败：清除Token，递增失败计数"""
        self._auth_fail_count += 1
        self._state.last_error = reason

        config = config_manager.config.remote
        config.token = ""
        config.token_expires = 0
        config_manager.save()

        if self._auth_fail_count >= self._MAX_AUTH_RETRIES:
            self._auth_stopped = True
            logger.error(
                f"鉴权连续失败 {self._auth_fail_count} 次，停止重连: {reason}"
            )
        else:
            logger.warning(
                f"疑似鉴权失败，清除Token等待重新登录 "
                f"({self._auth_fail_count}/{self._MAX_AUTH_RETRIES}): {reason}"
            )

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
            self._auth_fail_count += 1
            self._state.last_error = "Token 已失效"
            if self._auth_fail_count >= self._MAX_AUTH_RETRIES:
                self._auth_stopped = True
                logger.error(
                    f"鉴权连续失败 {self._auth_fail_count} 次，停止重连"
                )
            else:
                logger.warning(
                    f"收到 401 错误，清除 Token 等待重连 ({self._auth_fail_count}/{self._MAX_AUTH_RETRIES})"
                )
            config_manager.config.remote.token = ""
            config_manager.config.remote.token_expires = 0
            config_manager.save()
            # 断开当前连接，触发重连
            if self._ws and not self._ws.closed:
                await self._ws.close()
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

    def _schedule_reconnect(self):
        """调度重连任务，保证同一时间最多只有一个重连任务在运行。

        旧的实现里 receive_loop 与 _reconnect 都会 create_task(self._reconnect)，
        一旦出现并发就会各自繁殖，形成多条重连链互相取消。这里通过
        _reconnect_task 做单例约束，从根上杜绝并发重连。
        """
        if not self._running or self._auth_stopped:
            if self._auth_stopped:
                logger.warning("鉴权失败次数已达上限，不再重连。请检查配置后重启服务")
            return
        if self._reconnect_task and not self._reconnect_task.done():
            return  # 已有重连任务在排队，避免并发重连链
        self._reconnect_task = asyncio.create_task(self._reconnect())

    async def _reconnect(self):
        """重连逻辑（指数退避）。单任务循环，避免多条重连链并发。"""
        try:
            while self._running and not self._auth_stopped:
                self._state.reconnect_attempts += 1

                # 计算延迟（指数退避：1, 2, 4, 8, 16, 30...）
                delay = min(2 ** (self._state.reconnect_attempts - 1), self._max_reconnect_delay)
                logger.info(f"将在 {delay} 秒后尝试第 {self._state.reconnect_attempts} 次重连...")

                await asyncio.sleep(delay)

                if not self._running or self._auth_stopped:
                    break

                # 尝试重连，成功则退出循环；真正断线时由 receive_loop 重新调度
                success = await self.connect()
                if success:
                    break
        except asyncio.CancelledError:
            pass
        finally:
            self._reconnect_task = None

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
        self._auth_fail_count = 0
        self._auth_stopped = False

        success = await self.connect()
        if not success:
            logger.warning(f"初始连接失败: {self._state.last_error}")
            # 启动重连
            self._schedule_reconnect()
    
    async def stop(self):
        """停止客户端"""
        self._running = False
        await self.disconnect()


# 全局远程 WebSocket 客户端实例
remote_ws_client = RemoteWebSocketClient()
