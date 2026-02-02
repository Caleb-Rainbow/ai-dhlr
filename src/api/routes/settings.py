"""
系统设置API
"""
from fastapi import APIRouter
from pydantic import BaseModel
from typing import Optional, Dict

from ...utils.config import config_manager


router = APIRouter(prefix="/settings", tags=["settings"])


class AlarmSettings(BaseModel):
    """报警设置（三阶段）"""
    warning_time: int
    alarm_time: int
    action_time: int
    broadcast_interval: int  # 语音播报间隔（秒）
    warning_message: str
    alarm_message: str
    action_message: str


class SystemInfo(BaseModel):
    """系统信息"""
    name: str
    version: str
    device_id: str
    debug: bool


class NetworkStatus(BaseModel):
    """网络状态"""
    interface_type: str  # "wifi" | "ethernet" | "unknown"
    interface_name: str
    ip_address: str
    signal_strength: int
    gateway: str
    is_connected: bool


class RemoteServerSettings(BaseModel):
    """远程服务器设置"""
    enabled: bool
    server_url: str
    websocket_path: str = "dhlr/socket"
    login_path: str = "/login"
    username: str = ""
    password: str = ""


class RemoteServerStatus(BaseModel):
    """远程服务器状态"""
    enabled: bool
    is_connected: bool
    is_connecting: bool
    last_error: str
    reconnect_attempts: int


class LoginVerifyRequest(BaseModel):
    """登录校验请求"""
    server_url: str
    login_path: str = "/login"
    username: str
    password: str



# ==================== 报警设置（三阶段） ====================

@router.get("/alarm", response_model=AlarmSettings)
async def get_alarm_settings():
    """获取报警设置"""
    alarm = config_manager.config.alarm
    return AlarmSettings(
        warning_time=alarm.warning_time,
        alarm_time=alarm.alarm_time,
        action_time=alarm.action_time,
        broadcast_interval=alarm.broadcast_interval,
        warning_message=alarm.warning_message,
        alarm_message=alarm.alarm_message,
        action_message=alarm.action_message
    )


@router.post("/alarm")
async def update_alarm_settings(settings: AlarmSettings):
    """更新报警设置"""
    alarm = config_manager.config.alarm
    alarm.warning_time = settings.warning_time
    alarm.alarm_time = settings.alarm_time
    alarm.action_time = settings.action_time
    alarm.broadcast_interval = settings.broadcast_interval
    alarm.warning_message = settings.warning_message
    alarm.alarm_message = settings.alarm_message
    alarm.action_message = settings.action_message
    config_manager.save()

    return {"success": True, "message": "报警设置已更新"}


# ==================== 系统信息 ====================

@router.get("/system", response_model=SystemInfo)
async def get_system_info():
    """获取系统信息"""
    system = config_manager.config.system
    return SystemInfo(
        name=system.name,
        version=system.version,
        device_id=system.device_id or "未生成",
        debug=system.debug
    )


# ==================== 网络状态 ====================

@router.get("/network", response_model=NetworkStatus)
async def get_network_status():
    """获取网络状态"""
    try:
        from ...utils.network_monitor import network_monitor
        status = network_monitor.update_status()
        return NetworkStatus(
            interface_type=status.interface_type,
            interface_name=status.interface_name,
            ip_address=status.ip_address,
            signal_strength=status.signal_strength,
            gateway=status.gateway,
            is_connected=status.is_connected
        )
    except Exception as e:
        return NetworkStatus(
            interface_type="unknown",
            interface_name="",
            ip_address="",
            signal_strength=-1,
            gateway="",
            is_connected=False
        )


# ==================== 远程服务器配置 ====================

@router.get("/remote")
async def get_remote_settings():
    """获取远程服务器配置"""
    remote = config_manager.config.remote
    
    # 获取连接状态
    is_connected = False
    is_connecting = False
    last_error = ""
    reconnect_attempts = 0
    
    try:
        from ..websocket_client import remote_ws_client
        state = remote_ws_client.state
        is_connected = state.is_connected
        is_connecting = state.is_connecting
        last_error = state.last_error
        reconnect_attempts = state.reconnect_attempts
    except Exception:
        pass
    
    return {
        "enabled": remote.enabled,
        "server_url": remote.server_url,
        "websocket_path": remote.websocket_path,
        "login_path": remote.login_path,
        "username": remote.username,
        "has_token": bool(remote.token),
        "is_connected": is_connected,
        "is_connecting": is_connecting,
        "last_error": last_error,
        "reconnect_attempts": reconnect_attempts
    }


@router.post("/remote")
async def update_remote_settings(settings: RemoteServerSettings):
    """更新远程服务器配置"""
    remote = config_manager.config.remote
    
    # 更新配置
    remote.enabled = settings.enabled
    remote.server_url = settings.server_url
    remote.websocket_path = settings.websocket_path
    remote.login_path = settings.login_path
    remote.username = settings.username
    
    # 如果提供了新密码，更新密码并清除Token
    if settings.password:
        remote.password = settings.password
        remote.token = ""  # 清除旧Token
    
    config_manager.save()
    
    # 如果启用，尝试重新连接
    if settings.enabled:
        try:
            from ..websocket_client import remote_ws_client
            import asyncio
            # 先断开旧连接
            await remote_ws_client.stop()
            # 启动新连接
            asyncio.create_task(remote_ws_client.start())
        except Exception as e:
            return {"success": True, "message": f"配置已保存，但连接启动失败: {e}"}
    else:
        # 禁用时断开连接
        try:
            from ..websocket_client import remote_ws_client
            await remote_ws_client.stop()
        except Exception:
            pass
    
    return {"success": True, "message": "远程服务器配置已更新"}


@router.post("/remote/verify")
async def verify_remote_login(request: LoginVerifyRequest):
    """校验远程服务器登录凭证"""
    import aiohttp
    from urllib.parse import urlparse
    
    server_url = request.server_url.strip()
    if not server_url:
        return {"success": False, "message": "服务器地址不能为空"}
    
    # 确保有协议前缀
    if not server_url.startswith(('http://', 'https://')):
        server_url = 'http://' + server_url
    
    parsed = urlparse(server_url)
    login_path = request.login_path.strip()
    if not login_path.startswith('/'):
        login_path = '/' + login_path
    
    login_url = f"{parsed.scheme}://{parsed.netloc}{login_path}"
    
    try:
        timeout = aiohttp.ClientTimeout(total=10)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            payload = {
                "username": request.username,
                "password": request.password
            }
            
            async with session.post(login_url, json=payload) as response:
                if response.status == 200:
                    data = await response.json()
                    if data.get('code') == 200:
                        token = data.get('token', '')
                        if token:
                            # 保存配置
                            remote = config_manager.config.remote
                            remote.server_url = request.server_url
                            remote.login_path = request.login_path
                            remote.username = request.username
                            remote.password = request.password
                            remote.token = token
                            config_manager.save()
                            
                            return {
                                "success": True, 
                                "message": "校验成功",
                                "token": token[:20] + "..."  # 只返回部分token
                            }
                        else:
                            return {"success": False, "message": "响应中无 Token"}
                    else:
                        msg = data.get('msg', '登录失败')
                        return {"success": False, "message": msg}
                elif response.status == 401:
                    return {"success": False, "message": "用户名或密码错误"}
                else:
                    text = await response.text()
                    return {"success": False, "message": f"HTTP {response.status}: {text[:100]}"}
                    
    except Exception as e:
        return {"success": False, "message": f"连接失败: {str(e)}"}


@router.get("/websocket/status")
async def get_websocket_status():
    """获取 WebSocket 连接状态"""
    try:
        from ..websocket import message_dispatcher
        return message_dispatcher.get_status()
    except Exception as e:
        return {"error": str(e)}
