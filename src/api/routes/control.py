"""
控制API
远程复位、模拟开火等控制接口
"""
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from ...zone.state_machine import zone_manager
from ...output.gpio import gpio_controller
from ...utils.logger import get_logger

router = APIRouter(prefix="/control", tags=["control"])
logger = get_logger()


class FireStateRequest(BaseModel):
    """开火状态请求"""
    is_on: bool


@router.post("/reset/{zone_id}")
async def reset_zone(zone_id: str):
    """
    远程复位灶台
    
    用于从预警或切电状态恢复
    """
    sm = zone_manager.get_zone(zone_id)
    if not sm:
        raise HTTPException(status_code=404, detail="灶台不存在")
    
    success = sm.reset()
    
    if success:
        # 恢复供电
        gpio_controller.restore(zone_id)
        return {"success": True, "message": f"灶台 {zone_id} 已复位并恢复供电"}
    else:
        return {"success": False, "message": "当前状态无需复位"}


@router.post("/fire/{zone_id}")
async def set_fire_state(zone_id: str, request: FireStateRequest):
    """
    设置开火状态（Demo用）
    
    模拟GPIO信号，用于测试
    """
    sm = zone_manager.get_zone(zone_id)
    if not sm:
        raise HTTPException(status_code=404, detail="灶台不存在")
    
    zone_manager.set_fire_state(zone_id, request.is_on)
    
    state_text = "开火" if request.is_on else "关火"
    logger.info(f"[模拟] 灶台 {zone_id} 设置为: {state_text}")
    
    return {
        "success": True,
        "message": f"灶台 {zone_id} 已设置为{state_text}状态",
        "is_fire_on": request.is_on
    }


@router.get("/fire/{zone_id}")
async def get_fire_state(zone_id: str):
    """获取开火状态"""
    sm = zone_manager.get_zone(zone_id)
    if not sm:
        raise HTTPException(status_code=404, detail="灶台不存在")
    
    is_on = zone_manager.get_fire_state(zone_id)
    return {
        "zone_id": zone_id,
        "is_fire_on": is_on
    }


@router.post("/cutoff/{zone_id}")
async def manual_cutoff(zone_id: str):
    """手动切电"""
    sm = zone_manager.get_zone(zone_id)
    if not sm:
        raise HTTPException(status_code=404, detail="灶台不存在")
    
    success = gpio_controller.cutoff(zone_id)
    
    return {
        "success": success,
        "message": f"灶台 {zone_id} 已手动切电" if success else "切电操作失败"
    }


@router.post("/restore/{zone_id}")
async def manual_restore(zone_id: str):
    """手动恢复供电"""
    sm = zone_manager.get_zone(zone_id)
    if not sm:
        raise HTTPException(status_code=404, detail="灶台不存在")
    
    success = gpio_controller.restore(zone_id)
    
    return {
        "success": success,
        "message": f"灶台 {zone_id} 已恢复供电" if success else "恢复供电操作失败"
    }
