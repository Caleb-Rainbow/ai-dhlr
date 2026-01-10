"""
性能监控模块
用于监控系统运行时的性能指标
"""
import time
import threading
from typing import Optional, Dict, List
from collections import deque
from dataclasses import dataclass, field

from .logger import get_logger


@dataclass
class PerformanceStats:
    """性能统计数据"""
    inference_time_ms: float = 0
    avg_inference_time_ms: float = 0
    min_inference_time_ms: float = 0
    max_inference_time_ms: float = 0
    fps: float = 0
    cpu_percent: float = 0
    memory_mb: float = 0
    npu_load: float = 0  # NPU 负载百分比
    sample_count: int = 0


class PerformanceMonitor:
    """
    性能监控器
    
    用于收集和统计系统性能指标：
    - 推理时间
    - FPS
    - CPU使用率
    - 内存使用
    """
    
    _instance: Optional['PerformanceMonitor'] = None
    
    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance
    
    def __init__(self):
        if hasattr(self, '_initialized'):
            return
        self._initialized = True
        
        self._logger = get_logger()
        
        # 推理时间记录（滑动窗口）
        self._inference_times: deque = deque(maxlen=100)
        
        # 帧处理时间记录
        self._frame_times: deque = deque(maxlen=30)
        self._last_frame_time: float = 0
        
        # CPU/内存/NPU 监控
        self._cpu_percent: float = 0
        self._memory_mb: float = 0
        self._npu_load: float = 0
        
        # 监控线程
        self._monitor_thread: Optional[threading.Thread] = None
        self._running: bool = False
        
        # 锁
        self._lock = threading.Lock()
    
    def start(self):
        """启动性能监控"""
        if self._running:
            return
        
        self._running = True
        self._monitor_thread = threading.Thread(target=self._monitor_loop, daemon=True)
        self._monitor_thread.start()
        self._logger.info("性能监控已启动")
    
    def stop(self):
        """停止性能监控"""
        self._running = False
        if self._monitor_thread:
            self._monitor_thread.join(timeout=2.0)
        self._logger.info("性能监控已停止")
    
    def _monitor_loop(self):
        """监控循环"""
        try:
            import psutil
            has_psutil = True
        except ImportError:
            has_psutil = False
            self._logger.warning("未安装psutil，CPU/内存监控不可用")
        
        while self._running:
            try:
                if has_psutil:
                    # 获取CPU使用率
                    self._cpu_percent = psutil.cpu_percent(interval=None)
                    
                    # 获取内存使用
                    process = psutil.Process()
                    memory_info = process.memory_info()
                    self._memory_mb = memory_info.rss / (1024 * 1024)
                
                # 获取 NPU 负载 (RK3568/RK3588)
                self._npu_load = self._get_npu_load()

                time.sleep(1.0)
                
            except Exception as e:
                self._logger.warning(f"性能监控错误: {e}")
                time.sleep(5.0)
    
    def _get_npu_load(self) -> float:
        """获取 NPU 负载百分比 (RK3568/RK3588)"""
        try:
            from pathlib import Path
            npu_load_file = Path("/sys/kernel/debug/rknpu/load")
            
            if npu_load_file.exists():
                content = npu_load_file.read_text().strip()
                # 解析格式: "NPU load: 21%" 或 "Core0: 21%, Core1: 15%"
                import re
                matches = re.findall(r'(\d+)%', content)
                if matches:
                    # 取平均值（多核情况）
                    return sum(int(m) for m in matches) / len(matches)
        except PermissionError:
            # 需要 root 权限
            pass
        except Exception:
            pass
        return 0
    
    def record_inference_time(self, time_ms: float):
        """
        记录推理时间
        
        Args:
            time_ms: 推理时间（毫秒）
        """
        with self._lock:
            self._inference_times.append(time_ms)
    
    def record_frame(self):
        """记录帧处理"""
        current_time = time.time()
        if self._last_frame_time > 0:
            frame_time = (current_time - self._last_frame_time) * 1000
            with self._lock:
                self._frame_times.append(frame_time)
        self._last_frame_time = current_time
    
    def get_stats(self) -> PerformanceStats:
        """
        获取性能统计
        
        Returns:
            性能统计数据
        """
        with self._lock:
            inference_times = list(self._inference_times)
            frame_times = list(self._frame_times)
        
        stats = PerformanceStats()
        
        if inference_times:
            stats.inference_time_ms = round(inference_times[-1], 2)
            stats.avg_inference_time_ms = round(sum(inference_times) / len(inference_times), 2)
            stats.min_inference_time_ms = round(min(inference_times), 2)
            stats.max_inference_time_ms = round(max(inference_times), 2)
            stats.sample_count = len(inference_times)
            
            if stats.avg_inference_time_ms > 0:
                stats.fps = round(1000 / stats.avg_inference_time_ms, 1)
        
        stats.cpu_percent = round(self._cpu_percent, 1)
        stats.memory_mb = round(self._memory_mb, 1)
        stats.npu_load = round(self._npu_load, 1)
        
        return stats
    
    def get_stats_dict(self) -> Dict:
        """获取性能统计（字典格式）"""
        stats = self.get_stats()
        return {
            "inference_time_ms": stats.inference_time_ms,
            "avg_inference_time_ms": stats.avg_inference_time_ms,
            "min_inference_time_ms": stats.min_inference_time_ms,
            "max_inference_time_ms": stats.max_inference_time_ms,
            "fps": stats.fps,
            "cpu_percent": stats.cpu_percent,
            "memory_mb": stats.memory_mb,
            "npu_load": stats.npu_load,
            "sample_count": stats.sample_count
        }
    
    def reset(self):
        """重置统计数据"""
        with self._lock:
            self._inference_times.clear()
            self._frame_times.clear()
        self._last_frame_time = 0


# 全局单例
performance_monitor = PerformanceMonitor()
