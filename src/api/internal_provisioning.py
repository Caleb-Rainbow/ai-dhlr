"""内部配网接口（仅限本机回环 127.0.0.1 / ::1）。

供 ai-dhlr-ble.service（root，蓝牙配网服务）在 WiFi 切换成功后，把
remote / system 配置下发给 dhlr 应用热生效（落 config.yaml + 重连远程
链路），无需重启主程序。LAN 内其他主机访问返回 403。

安全边界：FastAPI 依赖 [require_loopback] 校验 request.client.host；
真实 TCP 源地址无法跨接口伪造回环，故该校验可靠。
"""
from __future__ import annotations

import asyncio
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel

from ..utils.config import config_manager, normalize_remote_websocket_path
from ..utils.logger import get_logger

logger = get_logger()

router = APIRouter(prefix="/internal", tags=["internal"])

_LOOPBACK = {"127.0.0.1", "::1", "localhost"}


def require_loopback(request: Request) -> None:
    """仅允许本机回环访问 /internal/*，否则 403。"""
    peer = request.client.host if request.client else ""
    if peer not in _LOOPBACK:
        raise HTTPException(status_code=403, detail="forbidden: loopback only")


class RemotePart(BaseModel):
    enabled: Optional[bool] = None
    server_url: Optional[str] = None
    websocket_path: Optional[str] = None
    login_path: Optional[str] = None
    username: Optional[str] = None
    password: Optional[str] = None
    token: Optional[str] = None


class SystemPart(BaseModel):
    name: Optional[str] = None
    device_id: Optional[str] = None


class ProvisioningPayload(BaseModel):
    remote: Optional[RemotePart] = None
    system: Optional[SystemPart] = None


@router.post("/apply-provisioning", dependencies=[Depends(require_loopback)])
async def apply_provisioning(payload: ProvisioningPayload) -> dict:
    """应用配网下发的 remote/system 配置并持久化。

    仅更新显式提供（非 None）的字段；未提供者保持不变。
    remote 变化且启用时触发远程链路重连（热生效）。
    """
    config = config_manager.config
    remote_changed = False
    identity_changed = False

    if payload.remote is not None:
        r = config.remote
        rp = payload.remote
        if rp.enabled is not None:
            r.enabled = rp.enabled
            remote_changed = True
        if rp.server_url is not None:
            r.server_url = rp.server_url
            remote_changed = True
        if rp.websocket_path is not None:
            r.websocket_path = normalize_remote_websocket_path(rp.websocket_path)
            remote_changed = True
        if rp.login_path is not None:
            r.login_path = rp.login_path
            remote_changed = True
        if rp.username is not None:
            r.username = rp.username
            remote_changed = True
        if rp.password:  # 非空才改；改密码清旧 token
            r.password = rp.password
            r.token = ""
            remote_changed = True
        if rp.token is not None:
            r.token = rp.token
            remote_changed = True

    if payload.system is not None:
        if payload.system.name is not None:
            config.system.name = payload.system.name
        if payload.system.device_id:  # 非空才改
            new_device_id = payload.system.device_id.strip()
            if new_device_id and new_device_id != config.system.device_id:
                config.system.device_id = new_device_id
                identity_changed = True

    config_manager.save()
    logger.info(
        f"配网配置已应用: remote_changed={remote_changed}, "
        f"name={config.system.name}, device_id={config.system.device_id}"
    )

    # 远程链路配置变化且启用 → 热重连
    remote_reconnect = False
    if (remote_changed or identity_changed) and config.remote.enabled:
        try:
            from .websocket_client import remote_ws_client

            await remote_ws_client.stop()
            asyncio.create_task(remote_ws_client.start())
            remote_reconnect = True
        except Exception as e:
            logger.warning(f"远程链路重连失败: {e}")

    return {"ok": True, "remote_reconnect": remote_reconnect}
