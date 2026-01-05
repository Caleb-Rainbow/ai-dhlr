"""
RKNN推理引擎
用于RK3568/RK3588平台的RKNN量化推理
"""
import time
from typing import List, Tuple, Optional
from pathlib import Path
from math import exp
import numpy as np
import cv2

from .engine import InferenceEngine, Detection
from ..utils.logger import get_logger


class RKNNEngine(InferenceEngine):
    """
    基于RKNN的推理引擎
    
    支持YOLOv8/YOLOv11模型的RKNN量化版本
    输出格式：6个张量（3个head，每个head有cls+reg）
    """
    
    PERSON_CLASS_ID = 0
    PERSON_CLASS_NAME = "person"
    
    # COCO类别名称
    CLASSES = [
        'person', 'bicycle', 'car', 'motorcycle', 'airplane', 'bus', 'train', 'truck', 'boat', 'traffic light',
        'fire hydrant', 'stop sign', 'parking meter', 'bench', 'bird', 'cat', 'dog', 'horse', 'sheep', 'cow',
        'elephant', 'bear', 'zebra', 'giraffe', 'backpack', 'umbrella', 'handbag', 'tie', 'suitcase', 'frisbee',
        'skis', 'snowboard', 'sports ball', 'kite', 'baseball bat', 'baseball glove', 'skateboard', 'surfboard',
        'tennis racket', 'bottle', 'wine glass', 'cup', 'fork', 'knife', 'spoon', 'bowl', 'banana', 'apple',
        'sandwich', 'orange', 'broccoli', 'carrot', 'hot dog', 'pizza', 'donut', 'cake', 'chair', 'couch',
        'potted plant', 'bed', 'dining table', 'toilet', 'tv', 'laptop', 'mouse', 'remote', 'keyboard', 'cell phone',
        'microwave', 'oven', 'toaster', 'sink', 'refrigerator', 'book', 'clock', 'vase', 'scissors', 'teddy bear',
        'hair drier', 'toothbrush'
    ]
    
    # 模型参数
    INPUT_WIDTH = 640
    INPUT_HEIGHT = 640
    HEAD_NUM = 3
    STRIDES = [8, 16, 32]
    MAP_SIZES = [[80, 80], [40, 40], [20, 20]]
    
    def __init__(self):
        self._rknn = None
        self._model_path: Optional[str] = None
        self._confidence_threshold = 0.5
        self._nms_threshold = 0.5
        self._logger = get_logger()
        self._meshgrid: List[float] = []
        self._class_num = len(self.CLASSES)
        
        # 性能统计
        self._inference_times: List[float] = []
        self._max_time_samples = 100
        
        # 预生成网格
        self._generate_meshgrid()
    
    def _generate_meshgrid(self):
        """生成网格坐标"""
        self._meshgrid = []
        for index in range(self.HEAD_NUM):
            for i in range(self.MAP_SIZES[index][0]):
                for j in range(self.MAP_SIZES[index][1]):
                    self._meshgrid.append(j + 0.5)
                    self._meshgrid.append(i + 0.5)
    
    def load_model(self, model_path: str, confidence_threshold: float = 0.5) -> bool:
        """
        加载RKNN模型
        
        Args:
            model_path: RKNN模型文件路径
            confidence_threshold: 置信度阈值
        
        Returns:
            是否加载成功
        """
        try:
            from rknnlite.api import RKNNLite
            
            path = Path(model_path)
            if not path.is_absolute():
                base_dir = Path(__file__).parent.parent.parent
                path = base_dir / model_path
            
            if not path.exists():
                self._logger.error(f"RKNN模型文件不存在: {path}")
                return False
            
            self._rknn = RKNNLite()
            
            # 加载模型
            ret = self._rknn.load_rknn(str(path))
            if ret != 0:
                self._logger.error(f"加载RKNN模型失败，错误码: {ret}")
                self._rknn = None
                return False
            
            # 初始化运行时
            ret = self._rknn.init_runtime()
            if ret != 0:
                self._logger.error(f"初始化RKNN运行时失败，错误码: {ret}")
                self._rknn.release()
                self._rknn = None
                return False
            
            self._model_path = str(path)
            self._confidence_threshold = confidence_threshold
            self._logger.info(f"RKNN模型已加载: {path}")
            return True
            
        except ImportError:
            self._logger.error("未安装rknnlite，请在RK3568/RK3588设备上安装rknn-toolkit-lite2")
            return False
        except Exception as e:
            self._logger.error(f"RKNN加载失败: {e}")
            import traceback
            self._logger.error(traceback.format_exc())
            return False
    
    def _preprocess(self, frame: np.ndarray) -> Tuple[np.ndarray, int, int]:
        """
        图像预处理
        
        Args:
            frame: 输入图像 (BGR格式)
        
        Returns:
            (处理后的图像, 原图高度, 原图宽度)
        """
        img_h, img_w = frame.shape[:2]
        
        # Resize到模型输入尺寸
        img = cv2.resize(frame, (self.INPUT_WIDTH, self.INPUT_HEIGHT), interpolation=cv2.INTER_LINEAR)
        
        # BGR -> RGB
        img = cv2.cvtColor(img, cv2.COLOR_BGR2RGB)
        
        # 添加batch维度 [H,W,C] -> [1,H,W,C]
        img = np.expand_dims(img, 0)
        
        return img, img_h, img_w
    
    def _sigmoid(self, x: float) -> float:
        """Sigmoid激活函数"""
        return 1 / (1 + exp(-x))
    
    def _postprocess(self, outputs: List[np.ndarray], img_h: int, img_w: int) -> List[Detection]:
        """
        后处理RKNN输出
        
        Args:
            outputs: RKNN推理输出（6个张量）
            img_h: 原图高度
            img_w: 原图宽度
        
        Returns:
            检测结果列表
        """
        detect_results = []
        
        # 展平输出
        output = []
        for i in range(len(outputs)):
            output.append(outputs[i].reshape((-1)))
        
        # 计算缩放比例
        scale_h = img_h / self.INPUT_HEIGHT
        scale_w = img_w / self.INPUT_WIDTH
        
        grid_index = -2
        
        for head_idx in range(self.HEAD_NUM):
            cls = output[head_idx * 2 + 0]  # 分类输出
            reg = output[head_idx * 2 + 1]  # 回归输出
            
            map_h = self.MAP_SIZES[head_idx][0]
            map_w = self.MAP_SIZES[head_idx][1]
            stride = self.STRIDES[head_idx]
            
            for h in range(map_h):
                for w in range(map_w):
                    grid_index += 2
                    
                    # 找最大类别分数
                    cls_max = -float('inf')
                    cls_index = 0
                    
                    if self._class_num == 1:
                        cls_max = self._sigmoid(cls[0 * map_h * map_w + h * map_w + w])
                        cls_index = 0
                    else:
                        for cl in range(self._class_num):
                            cls_val = cls[cl * map_h * map_w + h * map_w + w]
                            if cls_val > cls_max:
                                cls_max = cls_val
                                cls_index = cl
                        cls_max = self._sigmoid(cls_max)
                    
                    # 过滤低置信度
                    if cls_max <= self._confidence_threshold:
                        continue
                    
                    # DFL解码边界框
                    regdfl = []
                    for lc in range(4):
                        sfsum = 0
                        locval = 0
                        
                        # Softmax
                        for df in range(16):
                            idx = ((lc * 16) + df) * map_h * map_w + h * map_w + w
                            temp = exp(reg[idx])
                            reg[idx] = temp
                            sfsum += temp
                        
                        # 计算期望值
                        for df in range(16):
                            idx = ((lc * 16) + df) * map_h * map_w + h * map_w + w
                            sfval = reg[idx] / sfsum
                            locval += sfval * df
                        regdfl.append(locval)
                    
                    # 还原坐标
                    x1 = (self._meshgrid[grid_index + 0] - regdfl[0]) * stride
                    y1 = (self._meshgrid[grid_index + 1] - regdfl[1]) * stride
                    x2 = (self._meshgrid[grid_index + 0] + regdfl[2]) * stride
                    y2 = (self._meshgrid[grid_index + 1] + regdfl[3]) * stride
                    
                    # 缩放到原图尺寸
                    xmin = max(0, int(x1 * scale_w))
                    ymin = max(0, int(y1 * scale_h))
                    xmax = min(img_w, int(x2 * scale_w))
                    ymax = min(img_h, int(y2 * scale_h))
                    
                    cx = (xmin + xmax) // 2
                    cy = (ymin + ymax) // 2
                    
                    detect_results.append(Detection(
                        class_id=cls_index,
                        class_name=self.CLASSES[cls_index] if cls_index < len(self.CLASSES) else str(cls_index),
                        confidence=cls_max,
                        bbox=(xmin, ymin, xmax, ymax),
                        center=(cx, cy)
                    ))
        
        # NMS
        return self._nms(detect_results)
    
    def _nms(self, detections: List[Detection]) -> List[Detection]:
        """
        非极大值抑制
        
        Args:
            detections: 检测结果列表
        
        Returns:
            NMS后的检测结果
        """
        if not detections:
            return []
        
        # 按置信度排序
        sorted_dets = sorted(detections, key=lambda x: x.confidence, reverse=True)
        
        result = []
        valid = [True] * len(sorted_dets)
        
        for i in range(len(sorted_dets)):
            if not valid[i]:
                continue
            
            result.append(sorted_dets[i])
            
            for j in range(i + 1, len(sorted_dets)):
                if not valid[j]:
                    continue
                
                # 只对同类别进行NMS
                if sorted_dets[i].class_id != sorted_dets[j].class_id:
                    continue
                
                iou = self._compute_iou(sorted_dets[i], sorted_dets[j])
                if iou > self._nms_threshold:
                    valid[j] = False
        
        return result
    
    def _compute_iou(self, det1: Detection, det2: Detection) -> float:
        """计算两个检测框的IoU"""
        xmin = max(det1.x1, det2.x1)
        ymin = max(det1.y1, det2.y1)
        xmax = min(det1.x2, det2.x2)
        ymax = min(det1.y2, det2.y2)
        
        inner_w = max(0, xmax - xmin)
        inner_h = max(0, ymax - ymin)
        inner_area = inner_w * inner_h
        
        area1 = det1.area
        area2 = det2.area
        
        union = area1 + area2 - inner_area
        
        return inner_area / union if union > 0 else 0
    
    def detect(self, frame: np.ndarray) -> List[Detection]:
        """
        执行目标检测
        
        Args:
            frame: 输入图像 (BGR格式)
        
        Returns:
            检测结果列表
        """
        if not self.is_loaded:
            return []
        
        try:
            start_time = time.time()
            
            # 预处理
            img, img_h, img_w = self._preprocess(frame)
            
            # 推理
            outputs = self._rknn.inference(inputs=[img])
            
            # 后处理
            detections = self._postprocess(outputs, img_h, img_w)
            
            # 记录推理时间
            inference_time = (time.time() - start_time) * 1000
            self._record_inference_time(inference_time)
            
            return detections
            
        except Exception as e:
            self._logger.error(f"RKNN推理失败: {e}")
            import traceback
            self._logger.error(traceback.format_exc())
            return []
    
    def detect_persons(self, frame: np.ndarray) -> List[Detection]:
        """仅检测人员"""
        all_detections = self.detect(frame)
        return [d for d in all_detections if d.class_id == self.PERSON_CLASS_ID]
    
    def release(self):
        """释放资源"""
        if self._rknn:
            try:
                self._rknn.release()
            except Exception as e:
                self._logger.warning(f"释放RKNN资源时出错: {e}")
        self._rknn = None
        self._model_path = None
        self._inference_times.clear()
        self._logger.info("RKNN模型已释放")
    
    @property
    def is_loaded(self) -> bool:
        """模型是否已加载"""
        return self._rknn is not None
    
    def _record_inference_time(self, time_ms: float):
        """记录推理时间"""
        self._inference_times.append(time_ms)
        if len(self._inference_times) > self._max_time_samples:
            self._inference_times.pop(0)
    
    def get_performance_stats(self) -> dict:
        """
        获取性能统计
        
        Returns:
            性能统计字典
        """
        if not self._inference_times:
            return {
                "inference_time_ms": 0,
                "avg_inference_time_ms": 0,
                "min_inference_time_ms": 0,
                "max_inference_time_ms": 0,
                "fps": 0,
                "sample_count": 0
            }
        
        avg_time = sum(self._inference_times) / len(self._inference_times)
        
        return {
            "inference_time_ms": self._inference_times[-1] if self._inference_times else 0,
            "avg_inference_time_ms": round(avg_time, 2),
            "min_inference_time_ms": round(min(self._inference_times), 2),
            "max_inference_time_ms": round(max(self._inference_times), 2),
            "fps": round(1000 / avg_time, 1) if avg_time > 0 else 0,
            "sample_count": len(self._inference_times)
        }
