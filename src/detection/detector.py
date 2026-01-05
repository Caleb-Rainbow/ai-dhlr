"""
人形检测器
负责ROI区域人员判定和检测稳定性处理
"""
import threading
from typing import List, Dict, Tuple, Optional
from collections import deque
import numpy as np
import cv2

from .engine import InferenceEngine, Detection, create_inference_engine
from ..utils.config import InferenceConfig, DetectionConfig, ZoneConfig
from ..utils.logger import get_logger


def point_in_polygon(point: Tuple[int, int], polygon: List[Tuple[float, float]], 
                     frame_width: int, frame_height: int) -> bool:
    """
    判断点是否在多边形内
    
    Args:
        point: 点坐标 (x, y) 像素坐标
        polygon: 多边形顶点列表 (归一化坐标 0-1)
        frame_width: 图像宽度
        frame_height: 图像高度
    
    Returns:
        是否在多边形内
    """
    # 将归一化坐标转换为像素坐标
    polygon_pixels = np.array([
        [int(p[0] * frame_width), int(p[1] * frame_height)]
        for p in polygon
    ], dtype=np.int32)
    
    result = cv2.pointPolygonTest(polygon_pixels, point, False)
    return result >= 0


def detection_in_roi(detection: Detection, roi: List[Tuple[float, float]],
                     frame_width: int, frame_height: int, 
                     use_center: bool = True) -> bool:
    """
    判断检测结果是否在ROI区域内
    
    Args:
        detection: 检测结果
        roi: ROI区域顶点 (归一化坐标)
        frame_width: 图像宽度
        frame_height: 图像高度
        use_center: 是否使用中心点判定（否则使用整个bbox）
    
    Returns:
        是否在ROI内
    """
    if use_center:
        # 使用中心点判定
        return point_in_polygon(detection.center, roi, frame_width, frame_height)
    else:
        # 使用bbox判定：任意角点在ROI内即视为在内
        corners = [
            (detection.x1, detection.y1),
            (detection.x2, detection.y1),
            (detection.x1, detection.y2),
            (detection.x2, detection.y2)
        ]
        return any(point_in_polygon(c, roi, frame_width, frame_height) for c in corners)


class ZoneDetectionState:
    """灶台区域的检测状态（用于帧平滑处理）"""
    
    def __init__(self, zone_id: str, no_person_threshold: int = 3, 
                 person_present_threshold: int = 2):
        self.zone_id = zone_id
        self.no_person_threshold = no_person_threshold
        self.person_present_threshold = person_present_threshold
        
        # 最近N帧的检测结果
        self._recent_detections = deque(maxlen=max(no_person_threshold, person_present_threshold))
        self._current_state = False  # 当前是否有人
    
    def update(self, has_person: bool) -> bool:
        """
        更新检测状态
        
        Args:
            has_person: 当前帧是否检测到人
        
        Returns:
            平滑后的状态（是否有人）
        """
        self._recent_detections.append(has_person)
        
        if self._current_state:
            # 当前认为有人，需要连续N帧无人才判定为离开
            no_person_count = sum(1 for d in self._recent_detections if not d)
            if no_person_count >= self.no_person_threshold:
                self._current_state = False
        else:
            # 当前认为无人，需要连续N帧有人才判定为有人
            person_count = sum(1 for d in self._recent_detections if d)
            if person_count >= self.person_present_threshold:
                self._current_state = True
        
        return self._current_state
    
    @property
    def has_person(self) -> bool:
        """当前是否有人（平滑后）"""
        return self._current_state
    
    def reset(self):
        """重置状态"""
        self._recent_detections.clear()
        self._current_state = False


class PersonDetector:
    """人形检测器主类"""
    
    def __init__(self, inference_config: InferenceConfig, detection_config: DetectionConfig):
        self._inference_config = inference_config
        self._detection_config = detection_config
        self._engine: Optional[InferenceEngine] = None
        self._zone_states: Dict[str, ZoneDetectionState] = {}
        self._logger = get_logger()
        self._lock = threading.Lock()
    
    def initialize(self) -> bool:
        """初始化检测器"""
        try:
            # 创建推理引擎
            self._engine = create_inference_engine(self._inference_config.engine)
            
            # 加载模型
            success = self._engine.load_model(
                self._inference_config.model_path,
                self._inference_config.confidence_threshold
            )
            
            if success:
                self._logger.info("人形检测器初始化成功")
            else:
                self._logger.error("人形检测器初始化失败")
            
            return success
            
        except Exception as e:
            self._logger.error(f"初始化检测器失败: {e}")
            return False
    
    def register_zone(self, zone_config: ZoneConfig):
        """注册灶台区域"""
        with self._lock:
            self._zone_states[zone_config.id] = ZoneDetectionState(
                zone_id=zone_config.id,
                no_person_threshold=self._detection_config.no_person_threshold,
                person_present_threshold=self._detection_config.person_present_threshold
            )
            self._logger.info(f"注册检测区域: {zone_config.id}")
    
    def detect_frame(self, frame: np.ndarray) -> List[Detection]:
        """
        检测帧中的人员
        
        Args:
            frame: 图像帧
        
        Returns:
            person检测结果列表
        """
        if self._engine is None or not self._engine.is_loaded:
            return []
        
        import time
        start_time = time.time()
        
        result = self._engine.detect_persons(frame)
        
        # 记录推理时间到性能监控
        inference_time = (time.time() - start_time) * 1000
        try:
            from ..utils.performance import performance_monitor
            performance_monitor.record_inference_time(inference_time)
        except ImportError:
            pass
        
        return result
    
    def check_zone(self, zone_id: str, frame: np.ndarray, 
                   roi: List[Tuple[float, float]]) -> Tuple[bool, List[Detection]]:
        """
        检查指定区域是否有人
        
        Args:
            zone_id: 灶台ID
            frame: 图像帧
            roi: ROI区域坐标
        
        Returns:
            (是否有人(平滑后), 该区域内的检测结果列表)
        """
        # 执行检测
        persons = self.detect_frame(frame)
        
        if not persons:
            raw_has_person = False
            in_roi_persons = []
        else:
            # 检查哪些检测结果在ROI内
            h, w = frame.shape[:2]
            in_roi_persons = [
                p for p in persons
                if detection_in_roi(p, roi, w, h, use_center=True)
            ]
            raw_has_person = len(in_roi_persons) > 0
        
        # 更新帧平滑状态
        with self._lock:
            if zone_id not in self._zone_states:
                self._zone_states[zone_id] = ZoneDetectionState(
                    zone_id=zone_id,
                    no_person_threshold=self._detection_config.no_person_threshold,
                    person_present_threshold=self._detection_config.person_present_threshold
                )
            
            smoothed_has_person = self._zone_states[zone_id].update(raw_has_person)
        
        return smoothed_has_person, in_roi_persons
    
    def get_zone_state(self, zone_id: str) -> bool:
        """获取灶台区域的当前状态"""
        with self._lock:
            if zone_id in self._zone_states:
                return self._zone_states[zone_id].has_person
        return False
    
    def reset_zone(self, zone_id: str):
        """重置灶台区域状态"""
        with self._lock:
            if zone_id in self._zone_states:
                self._zone_states[zone_id].reset()
    
    def release(self):
        """释放资源"""
        if self._engine:
            self._engine.release()
            self._engine = None
        self._zone_states.clear()
        self._logger.info("人形检测器已释放")
    
    def draw_detections(self, frame: np.ndarray, detections: List[Detection],
                        roi: Optional[List[Tuple[float, float]]] = None) -> np.ndarray:
        """
        在帧上绘制检测结果和ROI
        
        Args:
            frame: 原始帧
            detections: 检测结果
            roi: ROI区域（可选）
        
        Returns:
            标注后的帧
        """
        annotated = frame.copy()
        h, w = frame.shape[:2]
        
        # 绘制ROI区域
        if roi:
            points = np.array([
                [int(p[0] * w), int(p[1] * h)]
                for p in roi
            ], np.int32)
            cv2.polylines(annotated, [points], True, (0, 255, 0), 2)
        
        # 绘制检测框
        for det in detections:
            color = (0, 255, 0) if det.class_id == 0 else (255, 0, 0)  # person用绿色
            cv2.rectangle(annotated, (det.x1, det.y1), (det.x2, det.y2), color, 2)
            
            # 绘制标签
            label = f"{det.class_name}: {det.confidence:.2f}"
            cv2.putText(annotated, label, (det.x1, det.y1 - 10),
                       cv2.FONT_HERSHEY_SIMPLEX, 0.5, color, 2)
            
            # 绘制中心点
            cv2.circle(annotated, det.center, 5, (0, 0, 255), -1)
        
        return annotated
