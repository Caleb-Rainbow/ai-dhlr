"""
语音测试 API
"""
from fastapi import APIRouter
from pydantic import BaseModel
from typing import Optional

from ...output.voice import voice_player

router = APIRouter(tags=["voice"])


class TTSTestRequest(BaseModel):
    """TTS测试请求"""
    text: Optional[str] = None


@router.post("/voice/test")
async def test_tts(request: TTSTestRequest = None):
    """
    测试语音播报
    
    Args:
        text: 可选的测试文本
    
    Returns:
        测试结果
    """
    text = request.text if request else None
    
    if not voice_player.is_enabled:
        return {
            "success": False,
            "message": "语音播报已禁用"
        }
    
    success = voice_player.test_speak(text)
    
    return {
        "success": success,
        "message": "语音测试已加入播放队列" if success else "语音测试失败"
    }


@router.get("/voice/status")
async def get_voice_status():
    """获取语音播报状态"""
    return {
        "enabled": voice_player.is_enabled
    }
