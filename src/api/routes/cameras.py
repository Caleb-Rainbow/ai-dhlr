"""
摄像头管理API
"""
from fastapi import APIRouter, HTTPException, Response
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from typing import Optional, List
import threading

from ...camera.manager import camera_manager, CameraStatus
from ...camera.stream import generate_mjpeg_stream, get_snapshot, get_snapshot_with_roi
from ...utils.config import CameraConfig, config_manager

router = APIRouter(prefix="/cameras", tags=["cameras"])


class CameraAddRequest(BaseModel):
    """添加摄像头请求"""
    id: str
    name: str
    type: str = "rtsp"  # usb 或 rtsp
    device: Optional[int] = None
    rtsp_url: Optional[str] = None
    username: Optional[str] = None
    password: Optional[str] = None
    width: int = 640
    height: int = 480
    fps: int = 30


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
    """添加摄像头
    
    注意：RTSP摄像头连接在后台进行，API立即返回。
    通过 /cameras 接口轮询摄像头状态确认连接结果。
    """
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
    if request.id != camera_id:
        raise HTTPException(status_code=400, detail="ID不匹配")
    
    # 检查摄像头是否存在
    existing_camera = camera_manager.get_camera(camera_id)
    if not existing_camera:
        raise HTTPException(status_code=404, detail="摄像头不存在")
    
    # 1. 停止并移除旧摄像头
    camera_manager.remove_camera(camera_id)
    config_manager.remove_camera(camera_id)
    
    # 2. 创建新配置
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
