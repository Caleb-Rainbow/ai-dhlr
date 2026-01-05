"""
系统设置API
"""
from fastapi import APIRouter
from pydantic import BaseModel

from ...utils.config import config_manager

router = APIRouter(prefix="/settings", tags=["settings"])


class SafetySettings(BaseModel):
    """安全设置"""
    warning_timeout: int
    cutoff_timeout: int


@router.get("/safety", response_model=SafetySettings)
async def get_safety_settings():
    """获取安全设置"""
    safety = config_manager.config.safety
    return SafetySettings(
        warning_timeout=safety.warning_timeout,
        cutoff_timeout=safety.cutoff_timeout
    )


@router.post("/safety")
async def update_safety_settings(settings: SafetySettings):
    """更新安全设置"""
    config_manager.config.safety.warning_timeout = settings.warning_timeout
    config_manager.config.safety.cutoff_timeout = settings.cutoff_timeout
    config_manager.save()
    
    return {"success": True, "message": "安全设置已更新"}
