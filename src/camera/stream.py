"""
MJPEG视频流服务
提供实时预览流和快照功能
"""
import cv2
import time
from typing import Generator, Optional
import numpy as np

from .manager import camera_manager


def generate_mjpeg_stream(camera_id: str, quality: int = 80) -> Generator[bytes, None, None]:
    """
    生成MJPEG流
    
    Args:
        camera_id: 摄像头ID
        quality: JPEG质量 (1-100)
    
    Yields:
        MJPEG帧数据
    """
    camera = camera_manager.get_camera(camera_id)
    if not camera:
        return
    
    encode_param = [int(cv2.IMWRITE_JPEG_QUALITY), quality]
    
    while True:
        frame = camera.get_frame()
        if frame is not None:
            # 编码为JPEG
            ret, jpeg = cv2.imencode('.jpg', frame, encode_param)
            if ret:
                yield (
                    b'--frame\r\n'
                    b'Content-Type: image/jpeg\r\n\r\n' + jpeg.tobytes() + b'\r\n'
                )
        time.sleep(0.033)  # ~30fps


def get_snapshot(camera_id: str, quality: int = 90) -> Optional[bytes]:
    """
    获取摄像头快照
    
    Args:
        camera_id: 摄像头ID
        quality: JPEG质量
    
    Returns:
        JPEG图像数据，失败返回None
    """
    camera = camera_manager.get_camera(camera_id)
    if not camera:
        return None
    
    frame = camera.get_snapshot()
    if frame is None:
        return None
    
    encode_param = [int(cv2.IMWRITE_JPEG_QUALITY), quality]
    ret, jpeg = cv2.imencode('.jpg', frame, encode_param)
    if ret:
        return jpeg.tobytes()
    return None


def get_snapshot_with_roi(camera_id: str, roi_points: list, quality: int = 90) -> Optional[bytes]:
    """
    获取带ROI标注的摄像头快照
    
    Args:
        camera_id: 摄像头ID
        roi_points: ROI区域点列表 (归一化坐标)
        quality: JPEG质量
    
    Returns:
        带ROI标注的JPEG图像数据
    """
    camera = camera_manager.get_camera(camera_id)
    if not camera:
        return None
    
    frame = camera.get_snapshot()
    if frame is None:
        return None
    
    # 绘制ROI区域
    h, w = frame.shape[:2]
    if roi_points and len(roi_points) >= 3:
        points = np.array([
            [int(p[0] * w), int(p[1] * h)] 
            for p in roi_points
        ], np.int32)
        cv2.polylines(frame, [points], True, (0, 255, 0), 2)
    
    encode_param = [int(cv2.IMWRITE_JPEG_QUALITY), quality]
    ret, jpeg = cv2.imencode('.jpg', frame, encode_param)
    if ret:
        return jpeg.tobytes()
    return None
