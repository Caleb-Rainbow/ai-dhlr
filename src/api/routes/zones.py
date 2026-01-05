"""
灶台与ROI配置API
"""
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from typing import List, Optional

from ...zone.state_machine import zone_manager
from ...utils.config import ZoneConfig, config_manager

router = APIRouter(prefix="/zones", tags=["zones"])


class RoiPoint(BaseModel):
    """ROI点"""
    x: float
    y: float



class ZoneConfigRequest(BaseModel):
    """灶台配置请求"""
    id: str
    name: str
    camera_id: str
    roi: List[List[float]]  # [[x1, y1], [x2, y2], ...]
    enabled: bool = True


class ZoneConfigResponse(BaseModel):
    """灶台配置响应"""
    id: str
    name: str
    camera_id: str
    roi: List[List[float]]
    enabled: bool


class ZoneUpdateRequest(BaseModel):
    """灶台更新请求"""
    name: Optional[str] = None
    camera_id: Optional[str] = None
    roi: Optional[List[List[float]]] = None
    enabled: Optional[bool] = None


@router.get("", response_model=List[ZoneConfigResponse])
async def get_zones():
    """获取所有灶台配置"""
    zones = zone_manager.get_all_zones()
    return [
        ZoneConfigResponse(
            id=z.zone.id,
            name=z.zone.name,
            camera_id=z.zone.camera_id,
            roi=[list(p) for p in z.zone.roi],
            enabled=z.zone.enabled
        )
        for z in zones
    ]


@router.get("/{zone_id}", response_model=ZoneConfigResponse)
async def get_zone(zone_id: str):
    """获取单个灶台配置"""
    sm = zone_manager.get_zone(zone_id)
    if not sm:
        raise HTTPException(status_code=404, detail="灶台不存在")
    
    z = sm.zone
    return ZoneConfigResponse(
        id=z.id,
        name=z.name,
        camera_id=z.camera_id,
        roi=[list(p) for p in z.roi],
        enabled=z.enabled
    )


@router.post("")
async def create_or_update_zone(request: ZoneConfigRequest):
    """
    创建或更新灶台配置
    
    这是App画完ROI框后调用的核心接口
    """
    # 转换ROI格式
    roi = [tuple(p) for p in request.roi]
    
    # 检查是否已存在
    existing = zone_manager.get_zone(request.id)
    
    if existing:
        # 更新现有灶台
        existing.update_config(roi=request.roi)
        existing.zone.name = request.name
        existing.zone.camera_id = request.camera_id
        existing.zone.enabled = request.enabled
        
        # 同步更新 config_manager 中的配置
        for zone_cfg in config_manager.config.zones:
            if zone_cfg.id == request.id:
                zone_cfg.name = request.name
                zone_cfg.camera_id = request.camera_id
                zone_cfg.roi = roi
                zone_cfg.enabled = request.enabled
                break
        
        message = f"灶台 {request.id} 配置已更新"
    else:
        # 创建新灶台
        config = ZoneConfig(
            id=request.id,
            name=request.name,
            camera_id=request.camera_id,
            roi=roi,
            enabled=request.enabled
        )
        
        # 同步添加到 config_manager
        config_manager.config.zones.append(config)
        
        # 需要从主程序获取回调函数
        from ...main import get_zone_callbacks
        callbacks = get_zone_callbacks()
        zone_manager.add_zone(config, **callbacks)
        
        message = f"灶台 {request.id} 已创建"
    
    # 保存到配置文件
    config_manager.save()
    
    return {"success": True, "message": message}


@router.put("/{zone_id}")
async def update_zone(zone_id: str, request: ZoneUpdateRequest):
    """更新灶台配置"""
    sm = zone_manager.get_zone(zone_id)
    if not sm:
        raise HTTPException(status_code=404, detail="灶台不存在")
    
    # 更新状态机中的配置
    if request.roi is not None:
        sm.update_config(roi=request.roi)
    
    if request.name is not None:
        sm.zone.name = request.name
    if request.camera_id is not None:
        sm.zone.camera_id = request.camera_id
    if request.enabled is not None:
        sm.zone.enabled = request.enabled
    
    # 同步更新config_manager中的配置
    for zone_config in config_manager.config.zones:
        if zone_config.id == zone_id:
            if request.name is not None:
                zone_config.name = request.name
            if request.camera_id is not None:
                zone_config.camera_id = request.camera_id
            if request.roi is not None:
                zone_config.roi = [tuple(p) for p in request.roi]
            if request.enabled is not None:
                zone_config.enabled = request.enabled
            break
    
    # 保存到配置文件
    config_manager.save()
    
    return {"success": True, "message": f"灶台 {zone_id} 已更新"}


@router.delete("/{zone_id}")
async def delete_zone(zone_id: str):
    """删除灶台"""
    sm = zone_manager.get_zone(zone_id)
    if not sm:
        raise HTTPException(status_code=404, detail="灶台不存在")
    
    # 从管理器移除
    if zone_id in zone_manager._zones:
        del zone_manager._zones[zone_id]
    if zone_id in zone_manager._fire_states:
        del zone_manager._fire_states[zone_id]
    
    # 从 config_manager 移除并保存
    config_manager.config.zones = [z for z in config_manager.config.zones if z.id != zone_id]
    config_manager.save()
    
    return {"success": True, "message": f"灶台 {zone_id} 已删除"}
