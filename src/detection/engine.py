"""
推理引擎抽象接口
定义统一的接口以支持PyTorch和RKNN的切换
"""
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import List, Tuple, Optional
import numpy as np


@dataclass
class Detection:
    """检测结果"""
    class_id: int           # 类别ID
    class_name: str         # 类别名称
    confidence: float       # 置信度
    bbox: Tuple[int, int, int, int]  # 边界框 (x1, y1, x2, y2)
    center: Tuple[int, int]  # 中心点 (cx, cy)
    
    @property
    def x1(self) -> int:
        return self.bbox[0]
    
    @property
    def y1(self) -> int:
        return self.bbox[1]
    
    @property
    def x2(self) -> int:
        return self.bbox[2]
    
    @property
    def y2(self) -> int:
        return self.bbox[3]
    
    @property
    def width(self) -> int:
        return self.x2 - self.x1
    
    @property
    def height(self) -> int:
        return self.y2 - self.y1
    
    @property
    def area(self) -> int:
        return self.width * self.height


class InferenceEngine(ABC):
    """推理引擎抽象基类"""
    
    @abstractmethod
    def load_model(self, model_path: str) -> bool:
        """
        加载模型
        
        Args:
            model_path: 模型文件路径
        
        Returns:
            是否加载成功
        """
        pass
    
    @abstractmethod
    def detect(self, frame: np.ndarray) -> List[Detection]:
        """
        执行检测
        
        Args:
            frame: 输入图像帧 (BGR格式)
        
        Returns:
            检测结果列表
        """
        pass
    
    @abstractmethod
    def detect_persons(self, frame: np.ndarray) -> List[Detection]:
        """
        仅检测人员
        
        Args:
            frame: 输入图像帧
        
        Returns:
            仅包含person类别的检测结果
        """
        pass
    
    @abstractmethod
    def release(self):
        """释放资源"""
        pass
    
    @property
    @abstractmethod
    def is_loaded(self) -> bool:
        """模型是否已加载"""
        pass


def create_inference_engine(engine_type: str) -> InferenceEngine:
    """
    工厂方法：创建推理引擎实例
    
    Args:
        engine_type: 引擎类型 ("pytorch" 或 "rknn")
    
    Returns:
        推理引擎实例
    """
    if engine_type == "pytorch":
        from .pytorch_engine import PyTorchEngine
        return PyTorchEngine()
    elif engine_type == "rknn":
        from .rknn_engine import RKNNEngine
        return RKNNEngine()
    else:
        raise ValueError(f"不支持的推理引擎类型: {engine_type}")
