"""
PyTorch推理引擎
使用Ultralytics YOLO进行推理
"""
from typing import List, Optional
from pathlib import Path
import numpy as np

from .engine import InferenceEngine, Detection
from ..utils.logger import get_logger


class PyTorchEngine(InferenceEngine):
    """基于PyTorch/Ultralytics的推理引擎"""
    
    # COCO数据集中person的类别ID
    PERSON_CLASS_ID = 0
    PERSON_CLASS_NAME = "person"
    
    def __init__(self):
        self._model = None
        self._model_path: Optional[str] = None
        self._confidence_threshold = 0.5
        self._logger = get_logger()
    
    def load_model(self, model_path: str, confidence_threshold: float = 0.5) -> bool:
        """加载YOLO模型"""
        try:
            from ultralytics import YOLO
            
            path = Path(model_path)
            # 如果不是绝对路径，则相对于项目根目录查找
            if not path.is_absolute():
                base_dir = Path(__file__).parent.parent.parent
                path = base_dir / model_path
            
            if not path.exists():
                self._logger.error(f"模型文件不存在: {path}")
                return False
            
            self._model = YOLO(str(path))
            self._model_path = str(path)
            self._confidence_threshold = confidence_threshold
            
            self._logger.info(f"PyTorch模型已加载: {path}")
            return True
            
        except Exception as e:
            self._logger.error(f"加载模型失败: {e}")
            import traceback
            self._logger.error(traceback.format_exc())
            return False
    
    def detect(self, frame: np.ndarray) -> List[Detection]:
        """执行目标检测（返回所有类别）"""
        if not self.is_loaded:
            return []
        
        try:
            results = self._model(frame, verbose=False, conf=self._confidence_threshold)
            detections = []
            
            for result in results:
                boxes = result.boxes
                if boxes is None:
                    continue
                
                for i in range(len(boxes)):
                    box = boxes[i]
                    class_id = int(box.cls[0].item())
                    confidence = float(box.conf[0].item())
                    
                    # 获取边界框坐标
                    x1, y1, x2, y2 = map(int, box.xyxy[0].tolist())
                    cx = (x1 + x2) // 2
                    cy = (y1 + y2) // 2
                    
                    # 获取类别名称
                    class_name = self._model.names.get(class_id, str(class_id))
                    
                    detections.append(Detection(
                        class_id=class_id,
                        class_name=class_name,
                        confidence=confidence,
                        bbox=(x1, y1, x2, y2),
                        center=(cx, cy)
                    ))
            
            return detections
            
        except Exception as e:
            self._logger.error(f"检测失败: {e}")
            return []
    
    def detect_persons(self, frame: np.ndarray) -> List[Detection]:
        """仅检测人员（过滤其他类别）"""
        all_detections = self.detect(frame)
        # 仅返回person类别
        return [d for d in all_detections if d.class_id == self.PERSON_CLASS_ID]
    
    def release(self):
        """释放资源"""
        self._model = None
        self._model_path = None
        self._logger.info("PyTorch模型已释放")
    
    @property
    def is_loaded(self) -> bool:
        """模型是否已加载"""
        return self._model is not None
