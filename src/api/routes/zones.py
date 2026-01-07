"""
灶台与ROI配置API
"""
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, field_validator
from typing import List, Optional
import uuid

from ...zone.state_machine import zone_manager
from ...utils.config import ZoneConfig, config_manager
from ...camera.manager import camera_manager

router = APIRouter(prefix="/zones", tags=["zones"])


class RoiPoint(BaseModel):
    """ROI点"""
    x: float
    y: float


class ZoneCreateRequest(BaseModel):
    """灶台创建请求 - 简化版，仅需name和camera_id"""
    name: str
    camera_id: str
    roi: Optional[List[List[float]]] = None  # 可选，默认为空
    enabled: bool = True
    
    @field_validator('name')
    @classmethod
    def validate_name(cls, v):
        if not v or not v.strip():
            raise ValueError('灶台名称不能为空')
        if len(v) > 50:
            raise ValueError('灶台名称长度不能超过50个字符')
        return v.strip()
    
    @field_validator('camera_id')
    @classmethod
    def validate_camera_id(cls, v):
        if not v or not v.strip():
            raise ValueError('摄像头ID不能为空')
        return v.strip()


class ZoneConfigRequest(BaseModel):
    """灶台配置请求 - 完整版，用于App画完ROI框后调用"""
    id: str
    name: str
    camera_id: str
    roi: List[List[float]]  # [[x1, y1], [x2, y2], ...]
    enabled: bool = True
    
    @field_validator('id')
    @classmethod
    def validate_id(cls, v):
        if not v or not v.strip():
            raise ValueError('灶台ID不能为空')
        return v.strip()
    
    @field_validator('name')
    @classmethod
    def validate_name(cls, v):
        if not v or not v.strip():
            raise ValueError('灶台名称不能为空')
        if len(v) > 50:
            raise ValueError('灶台名称长度不能超过50个字符')
        return v.strip()
    
    @field_validator('camera_id')
    @classmethod
    def validate_camera_id(cls, v):
        if not v or not v.strip():
            raise ValueError('摄像头ID不能为空')
        return v.strip()
    
    @field_validator('roi')
    @classmethod
    def validate_roi(cls, v):
        if v is None:
            return []
        for i, point in enumerate(v):
            if len(point) != 2:
                raise ValueError(f'ROI第{i+1}个点格式错误，应为[x, y]')
            x, y = point
            if not (0 <= x <= 1 and 0 <= y <= 1):
                raise ValueError(f'ROI第{i+1}个点坐标超出范围，应在0-1之间')
        return v


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
    
    @field_validator('name')
    @classmethod
    def validate_name(cls, v):
        if v is not None:
            if not v.strip():
                raise ValueError('灶台名称不能为空')
            if len(v) > 50:
                raise ValueError('灶台名称长度不能超过50个字符')
            return v.strip()
        return v
    
    @field_validator('camera_id')
    @classmethod
    def validate_camera_id(cls, v):
        if v is not None and not v.strip():
            raise ValueError('摄像头ID不能为空')
        return v.strip() if v else v
    
    @field_validator('roi')
    @classmethod
    def validate_roi(cls, v):
        if v is not None:
            for i, point in enumerate(v):
                if len(point) != 2:
                    raise ValueError(f'ROI第{i+1}个点格式错误，应为[x, y]')
                x, y = point
                if not (0 <= x <= 1 and 0 <= y <= 1):
                    raise ValueError(f'ROI第{i+1}个点坐标超出范围，应在0-1之间')
        return v


class ErrorResponse(BaseModel):
    """错误响应"""
    success: bool = False
    error: str
    detail: Optional[str] = None


def _generate_zone_id() -> str:
    """生成唯一的灶台ID"""
    existing_ids = {z.zone.id for z in zone_manager.get_all_zones()}
    # 尝试使用 zone_1, zone_2 格式
    for i in range(1, 100):
        zone_id = f"zone_{i}"
        if zone_id not in existing_ids:
            return zone_id
    # fallback 到 UUID
    return f"zone_{uuid.uuid4().hex[:8]}"


def _validate_camera_exists(camera_id: str) -> None:
    """验证摄像头是否存在"""
    camera = camera_manager.get_camera(camera_id)
    if not camera:
        raise HTTPException(
            status_code=400, 
            detail=f"摄像头 '{camera_id}' 不存在，请检查摄像头配置"
        )


def _validate_zone_name_unique(name: str, exclude_zone_id: str = None) -> None:
    """验证灶台名称是否唯一"""
    name_lower = name.strip().lower()
    for z in zone_manager.get_all_zones():
        # 如果是更新操作，排除当前灶台自身
        if exclude_zone_id and z.zone.id == exclude_zone_id:
            continue
        if z.zone.name.strip().lower() == name_lower:
            raise HTTPException(
                status_code=400,
                detail=f"灶台名称 '{name}' 已存在，请使用其他名称"
            )


@router.get("", response_model=List[ZoneConfigResponse])
async def get_zones():
    """获取所有灶台配置"""
    try:
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
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"获取灶台列表失败: {str(e)}")


@router.get("/{zone_id}", response_model=ZoneConfigResponse)
async def get_zone(zone_id: str):
    """获取单个灶台配置"""
    if not zone_id or not zone_id.strip():
        raise HTTPException(status_code=400, detail="灶台ID不能为空")
    
    sm = zone_manager.get_zone(zone_id)
    if not sm:
        raise HTTPException(status_code=404, detail=f"灶台 '{zone_id}' 不存在")
    
    z = sm.zone
    return ZoneConfigResponse(
        id=z.id,
        name=z.name,
        camera_id=z.camera_id,
        roi=[list(p) for p in z.roi],
        enabled=z.enabled
    )


@router.post("")
async def create_zone(request: ZoneCreateRequest):
    """
    创建灶台配置
    
    简化版接口，只需提供 name 和 camera_id，系统自动生成 id。
    ROI 可稍后通过 PUT 接口或 ROI 编辑器添加。
    
    请求参数:
    - name: 灶台名称（必填）
    - camera_id: 关联摄像头ID（必填）
    - roi: ROI区域坐标（可选，默认为空）
    - enabled: 是否启用（可选，默认true）
    
    可能的错误:
    - 400: 参数验证失败（名称为空、摄像头不存在等）
    - 500: 服务器内部错误
    """
    try:
        # 验证摄像头存在
        _validate_camera_exists(request.camera_id)
        
        # 验证名称唯一
        _validate_zone_name_unique(request.name)
        
        # 生成唯一ID
        zone_id = _generate_zone_id()
        
        # 处理ROI
        roi = [tuple(p) for p in (request.roi or [])]
        
        # 创建配置
        config = ZoneConfig(
            id=zone_id,
            name=request.name,
            camera_id=request.camera_id,
            roi=roi,
            enabled=request.enabled
        )
        
        # 添加到 config_manager
        config_manager.config.zones.append(config)
        
        # 从主程序获取回调函数
        try:
            from ...main import get_zone_callbacks
            callbacks = get_zone_callbacks()
        except ImportError as e:
            raise HTTPException(
                status_code=500, 
                detail=f"无法获取灶台回调函数: {str(e)}"
            )
        
        zone_manager.add_zone(config, **callbacks)
        
        # 保存配置
        config_manager.save()
        
        # 触发语音预合成
        try:
            from ...tts.tts_manager import tts_manager
            if config_manager.config.tts.enabled:
                tts_manager.submit_synthesis_task(zone_id, request.name)
        except Exception:
            pass  # TTS失败不影响主流程
        
        return {
            "success": True, 
            "message": f"灶台 '{request.name}' 创建成功",
            "id": zone_id,
            "data": {
                "id": zone_id,
                "name": request.name,
                "camera_id": request.camera_id,
                "roi": request.roi or [],
                "enabled": request.enabled
            }
        }
    except HTTPException:
        raise
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"创建灶台失败: {str(e)}")


@router.post("/full")
async def create_or_update_zone_full(request: ZoneConfigRequest):
    """
    创建或更新灶台配置（完整版）
    
    这是App画完ROI框后调用的核心接口，需要提供完整的配置信息。
    如果ID已存在则更新，否则创建新灶台。
    
    请求参数:
    - id: 灶台ID（必填）
    - name: 灶台名称（必填）
    - camera_id: 关联摄像头ID（必填）
    - roi: ROI区域坐标（必填，可为空数组）
    - enabled: 是否启用（可选，默认true）
    """
    try:
        # 验证摄像头存在
        _validate_camera_exists(request.camera_id)
        
        # 转换ROI格式
        roi = [tuple(p) for p in request.roi]
        
        # 检查是否已存在
        existing = zone_manager.get_zone(request.id)
        
        # 验证名称唯一（更新时排除自身）
        _validate_zone_name_unique(request.name, exclude_zone_id=request.id if existing else None)
        
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
            
            message = f"灶台 '{request.name}' 配置已更新"
            is_new = False
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
            
            # 从主程序获取回调函数
            try:
                from ...main import get_zone_callbacks
                callbacks = get_zone_callbacks()
            except ImportError as e:
                raise HTTPException(
                    status_code=500, 
                    detail=f"无法获取灶台回调函数: {str(e)}"
                )
            
            zone_manager.add_zone(config, **callbacks)
            
            message = f"灶台 '{request.name}' 已创建"
            is_new = True
        
        # 保存到配置文件
        config_manager.save()
        
        # 触发语音预合成（新建灶台或名称变更时）
        try:
            from ...tts.tts_manager import tts_manager
            if config_manager.config.tts.enabled:
                if is_new or not tts_manager.has_audio_files(request.id):
                    tts_manager.submit_synthesis_task(request.id, request.name)
        except Exception:
            pass  # TTS失败不影响主流程
        
        return {
            "success": True, 
            "message": message,
            "id": request.id,
            "is_new": is_new,
            "data": {
                "id": request.id,
                "name": request.name,
                "camera_id": request.camera_id,
                "roi": request.roi,
                "enabled": request.enabled
            }
        }
    except HTTPException:
        raise
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"操作失败: {str(e)}")


@router.put("/{zone_id}")
async def update_zone(zone_id: str, request: ZoneUpdateRequest):
    """
    更新灶台配置
    
    支持部分更新，只需传入需要修改的字段。
    
    请求参数（均为可选）:
    - name: 灶台名称
    - camera_id: 关联摄像头ID
    - roi: ROI区域坐标
    - enabled: 是否启用
    
    可能的错误:
    - 400: 参数验证失败（灶台ID为空、摄像头不存在等）
    - 404: 灶台不存在
    - 500: 服务器内部错误
    """
    try:
        if not zone_id or not zone_id.strip():
            raise HTTPException(status_code=400, detail="灶台ID不能为空")
        
        sm = zone_manager.get_zone(zone_id)
        if not sm:
            raise HTTPException(status_code=404, detail=f"灶台 '{zone_id}' 不存在")
        
        # 如果更新摄像头ID，验证其存在
        if request.camera_id is not None:
            _validate_camera_exists(request.camera_id)
        
        # 如果更新名称，验证名称唯一性
        if request.name is not None:
            _validate_zone_name_unique(request.name, exclude_zone_id=zone_id)
        
        # 记录更新的字段
        updated_fields = []
        
        # 更新状态机中的配置
        if request.roi is not None:
            sm.update_config(roi=request.roi)
            updated_fields.append("roi")
        
        if request.name is not None:
            sm.zone.name = request.name
            updated_fields.append("name")
        if request.camera_id is not None:
            sm.zone.camera_id = request.camera_id
            updated_fields.append("camera_id")
        if request.enabled is not None:
            sm.zone.enabled = request.enabled
            updated_fields.append("enabled")
        
        if not updated_fields:
            return {
                "success": True, 
                "message": "没有需要更新的字段",
                "updated_fields": []
            }
        
        # 同步更新config_manager中的配置
        zone_found = False
        for zone_config in config_manager.config.zones:
            if zone_config.id == zone_id:
                zone_found = True
                if request.name is not None:
                    zone_config.name = request.name
                if request.camera_id is not None:
                    zone_config.camera_id = request.camera_id
                if request.roi is not None:
                    zone_config.roi = [tuple(p) for p in request.roi]
                if request.enabled is not None:
                    zone_config.enabled = request.enabled
                break
        
        if not zone_found:
            # 状态机存在但配置中不存在，可能是数据不一致
            raise HTTPException(
                status_code=500, 
                detail="配置数据不一致，请刷新页面后重试"
            )
        
        # 保存到配置文件
        config_manager.save()
        
        return {
            "success": True, 
            "message": f"灶台 '{zone_id}' 已更新",
            "updated_fields": updated_fields
        }
    except HTTPException:
        raise
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"更新灶台失败: {str(e)}")


@router.delete("/{zone_id}")
async def delete_zone(zone_id: str):
    """
    删除灶台
    
    可能的错误:
    - 400: 灶台ID为空
    - 404: 灶台不存在
    - 500: 服务器内部错误
    """
    try:
        if not zone_id or not zone_id.strip():
            raise HTTPException(status_code=400, detail="灶台ID不能为空")
        
        sm = zone_manager.get_zone(zone_id)
        if not sm:
            raise HTTPException(status_code=404, detail=f"灶台 '{zone_id}' 不存在")
        
        zone_name = sm.zone.name
        
        # 从管理器移除
        if zone_id in zone_manager._zones:
            del zone_manager._zones[zone_id]
        if zone_id in zone_manager._fire_states:
            del zone_manager._fire_states[zone_id]
        
        # 从 config_manager 移除并保存
        original_count = len(config_manager.config.zones)
        config_manager.config.zones = [z for z in config_manager.config.zones if z.id != zone_id]
        config_manager.save()
        
        deleted = original_count > len(config_manager.config.zones)
        
        return {
            "success": True, 
            "message": f"灶台 '{zone_name}' 已删除",
            "deleted_id": zone_id,
            "deleted_name": zone_name
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"删除灶台失败: {str(e)}")
