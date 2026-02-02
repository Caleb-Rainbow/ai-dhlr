"""
摄像头管理API
"""
from fastapi import APIRouter, HTTPException, Response
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, field_validator
from typing import Optional, List
import threading

from ...camera.manager import camera_manager, CameraStatus
from ...camera.stream import generate_mjpeg_stream, get_snapshot, get_snapshot_with_roi
from ...utils.config import CameraConfig, config_manager

router = APIRouter(prefix="/cameras", tags=["cameras"])


class CameraAddRequest(BaseModel):
    """添加摄像头请求"""
    id: Optional[str] = None
    name: str
    type: str = "rtsp"  # usb 或 rtsp
    device: Optional[int] = None
    rtsp_url: Optional[str] = None
    username: Optional[str] = None
    password: Optional[str] = None
    width: int = 640
    height: int = 480
    fps: int = 30
    
    @field_validator('id')
    @classmethod
    def validate_id(cls, v):
        if v is None:
            return None
        if not v.strip():
            return None
        # 检查ID格式
        v = v.strip()
        if len(v) > 50:
            raise ValueError('摄像头ID长度不能超过50个字符')
        return v
    
    @field_validator('name')
    @classmethod
    def validate_name(cls, v):
        if not v or not v.strip():
            raise ValueError('摄像头名称不能为空')
        if len(v) > 100:
            raise ValueError('摄像头名称长度不能超过100个字符')
        return v.strip()
    
    @field_validator('type')
    @classmethod
    def validate_type(cls, v):
        if v not in ['usb', 'rtsp']:
            raise ValueError('摄像头类型必须是 "usb" 或 "rtsp"')
        return v
    
    @field_validator('width')
    @classmethod
    def validate_width(cls, v):
        if v < 160 or v > 4096:
            raise ValueError('宽度必须在160-4096之间')
        return v
    
    @field_validator('height')
    @classmethod
    def validate_height(cls, v):
        if v < 120 or v > 4096:
            raise ValueError('高度必须在120-4096之间')
        return v
    
    @field_validator('fps')
    @classmethod
    def validate_fps(cls, v):
        if v < 1 or v > 120:
            raise ValueError('帧率必须在1-120之间')
        return v


class CameraResponse(BaseModel):
    """摄像头响应"""
    id: str
    name: str
    type: str
    status: str
    width: int
    height: int
    fps: int
    device: Optional[int] = None
    rtsp_url: Optional[str] = None
    username: Optional[str] = None
    password: Optional[str] = None  # 添加密码字段


@router.get("", response_model=List[CameraResponse])
async def get_cameras():
    """获取摄像头列表"""
    cameras = camera_manager.get_all_cameras()
    return [
        CameraResponse(
            id=cam.id,
            name=cam.name,
            type=cam.type,
            status=cam.status.value,
            width=cam.config.width,
            height=cam.config.height,
            fps=cam.config.fps,
            device=cam.config.device,
            rtsp_url=cam.config.rtsp_url,
            username=cam.config.username,
            password=cam.config.password  # 返回密码
        )
        for cam in cameras
    ]


@router.get("/devices")
async def get_usb_devices():
    """获取可用USB摄像头设备"""
    return camera_manager.get_available_usb_cameras()


@router.post("", response_model=CameraResponse)
async def add_camera(request: CameraAddRequest):
    """
    添加摄像头
    
    请求参数:
    - id: 摄像头唯一ID（可选，未填则自动生成）
    - name: 摄像头名称（必填）
    - type: 类型，"usb" 或 "rtsp"（可选，默认rtsp）
    - device: USB设备索引（USB类型必填）
    - rtsp_url: RTSP地址（RTSP类型必填）
    - username/password: RTSP认证信息（可选）
    - width/height/fps: 分辨率和帧率（可选）
    
    可能的错误:
    - 400: 参数验证失败（ID重复、类型错误等）
    - 500: 服务器内部错误
    
    注意：RTSP摄像头连接在后台进行，API立即返回。
    通过 /cameras 接口轮询摄像头状态确认连接结果。
    """
    try:
        # 如果未提供ID，则自动生成
        if not request.id:
            # 获取现有所有ID
            existing_ids = {cam.id for cam in camera_manager.get_all_cameras()}
            # 查找最小可用整数ID（从0开始）
            next_id = 0
            while str(next_id) in existing_ids:
                next_id += 1
            request.id = str(next_id)
        
        # 检查ID是否已存在
        existing = camera_manager.get_camera(request.id)
        if existing:
            raise HTTPException(
                status_code=400, 
                detail=f"摄像头ID '{request.id}' 已存在，请使用其他ID"
            )
        
        # 检查名称是否已存在
        name_lower = request.name.strip().lower()
        for cam in camera_manager.get_all_cameras():
            if cam.name.strip().lower() == name_lower:
                raise HTTPException(
                    status_code=400,
                    detail=f"摄像头名称 '{request.name}' 已存在，请使用其他名称"
                )
        
        # 验证类型相关参数
        if request.type == 'usb' and request.device is None:
            raise HTTPException(
                status_code=400,
                detail="USB摄像头必须指定device设备索引"
            )
        
        if request.type == 'rtsp' and not request.rtsp_url:
            raise HTTPException(
                status_code=400,
                detail="RTSP摄像头必须指定rtsp_url地址"
            )
        
        # 创建配置
        config = CameraConfig(
            id=request.id,
            type=request.type,
            name=request.name,
            device=request.device,
            rtsp_url=request.rtsp_url,
            username=request.username,
            password=request.password,
            width=request.width,
            height=request.height,
            fps=request.fps
        )
        
        # 添加到管理器
        camera = camera_manager.add_camera(config)
        
        # 后台线程启动摄像头（避免阻塞 API）
        def start_camera_async():
            camera.start()
        
        thread = threading.Thread(target=start_camera_async, daemon=True)
        thread.start()
        
        # 保存到配置
        config_manager.add_camera(config)
        
        return CameraResponse(
            id=camera.id,
            name=camera.name,
            type=camera.type,
            status="connecting",
            width=config.width,
            height=config.height,
            fps=config.fps,
            device=config.device,
            rtsp_url=config.rtsp_url,
            username=config.username,
            password=config.password
        )
    except HTTPException:
        raise
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"添加摄像头失败: {str(e)}")


@router.delete("/{camera_id}")
async def delete_camera(camera_id: str):
    """删除摄像头"""
    success = camera_manager.remove_camera(camera_id)
    if not success:
        raise HTTPException(status_code=404, detail="摄像头不存在")
    
    # 从配置中移除
    config_manager.remove_camera(camera_id)
    
    return {"success": True, "message": f"摄像头 {camera_id} 已删除"}


@router.put("/{camera_id}", response_model=CameraResponse)
async def update_camera(camera_id: str, request: CameraAddRequest):
    """更新摄像头"""
    # 验证ID一致性
    if request.id and request.id != camera_id:
        raise HTTPException(status_code=400, detail="ID不匹配")
    
    # 检查摄像头是否存在
    existing_camera = camera_manager.get_camera(camera_id)
    if not existing_camera:
        raise HTTPException(status_code=404, detail="摄像头不存在")
    
    # 检查名称是否与其他摄像头重复（排除自身）
    name_lower = request.name.strip().lower()
    for cam in camera_manager.get_all_cameras():
        if cam.id != camera_id and cam.name.strip().lower() == name_lower:
            raise HTTPException(
                status_code=400,
                detail=f"摄像头名称 '{request.name}' 已存在，请使用其他名称"
            )
    
    # 1. 停止并移除旧摄像头
    camera_manager.remove_camera(camera_id)
    config_manager.remove_camera(camera_id)
    
    # 2. 创建新配置
    config = CameraConfig(
        id=request.id or camera_id,
        type=request.type,
        name=request.name,
        device=request.device,
        rtsp_url=request.rtsp_url,
        username=request.username,
        password=request.password,
        width=request.width,
        height=request.height,
        fps=request.fps
    )
    
    # 3. 添加新摄像头
    camera = camera_manager.add_camera(config)
    
    # 4. 后台线程启动摄像头
    def start_camera_async():
        camera.start()
    
    thread = threading.Thread(target=start_camera_async, daemon=True)
    thread.start()
    
    # 5. 保存新配置
    config_manager.add_camera(config)
    
    return CameraResponse(
        id=camera.id,
        name=camera.name,
        type=camera.type,
        status="connecting",
        width=config.width,
        height=config.height,
        fps=config.fps,
        device=config.device,
        rtsp_url=config.rtsp_url,
        username=config.username,
        password=config.password
    )


@router.get("/{camera_id}/preview")
async def get_preview(camera_id: str, stream: bool = False):
    """
    获取摄像头预览
    
    Args:
        camera_id: 摄像头ID
        stream: 是否返回MJPEG流，否则返回单帧快照
    """
    camera = camera_manager.get_camera(camera_id)
    if not camera:
        raise HTTPException(status_code=404, detail="摄像头不存在")
    
    if not camera.is_online:
        raise HTTPException(status_code=503, detail="摄像头离线")
    
    if stream:
        # 返回MJPEG流
        return StreamingResponse(
            generate_mjpeg_stream(camera_id),
            media_type="multipart/x-mixed-replace; boundary=frame"
        )
    else:
        # 返回单帧快照
        snapshot = get_snapshot(camera_id)
        if snapshot is None:
            raise HTTPException(status_code=500, detail="获取快照失败")
        return Response(content=snapshot, media_type="image/jpeg")


@router.get("/{camera_id}/status")
async def get_camera_status(camera_id: str):
    """获取摄像头状态"""
    camera = camera_manager.get_camera(camera_id)
    if not camera:
        raise HTTPException(status_code=404, detail="摄像头不存在")
    
    info = camera.get_info()
    return {
        "id": info.id,
        "name": info.name,
        "type": info.type,
        "status": info.status.value,
        "is_online": camera.is_online
    }
