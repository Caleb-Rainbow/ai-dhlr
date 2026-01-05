"""
日志工具模块
提供统一的日志记录和事件保存功能
"""
import os
import logging
import datetime
from pathlib import Path
from typing import Optional
import cv2
import numpy as np


class EventLogger:
    """事件日志记录器"""
    
    _instance: Optional['EventLogger'] = None
    
    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance
    
    def __init__(self):
        if hasattr(self, '_initialized'):
            return
        self._initialized = True
        self._logger = None
        self._log_dir = None
        self._snapshot_dir = None
    
    def setup(self, level: str = "INFO", log_dir: str = "logs", snapshot_dir: str = "snapshots"):
        """初始化日志配置"""
        # 创建目录
        base_dir = Path(__file__).parent.parent.parent
        self._log_dir = base_dir / log_dir
        self._snapshot_dir = base_dir / snapshot_dir
        
        self._log_dir.mkdir(parents=True, exist_ok=True)
        self._snapshot_dir.mkdir(parents=True, exist_ok=True)
        
        # 配置日志
        log_level = getattr(logging, level.upper(), logging.INFO)
        
        # 创建logger
        self._logger = logging.getLogger("fire_safety")
        self._logger.setLevel(log_level)
        
        # 清除已有handler
        self._logger.handlers.clear()
        
        # 控制台handler
        console_handler = logging.StreamHandler()
        console_handler.setLevel(log_level)
        console_format = logging.Formatter(
            '%(asctime)s [%(levelname)s] %(message)s',
            datefmt='%Y-%m-%d %H:%M:%S'
        )
        console_handler.setFormatter(console_format)
        self._logger.addHandler(console_handler)
        
        # 文件handler
        log_file = self._log_dir / f"fire_safety_{datetime.date.today()}.log"
        file_handler = logging.FileHandler(log_file, encoding='utf-8')
        file_handler.setLevel(log_level)
        file_format = logging.Formatter(
            '%(asctime)s [%(levelname)s] [%(filename)s:%(lineno)d] %(message)s',
            datefmt='%Y-%m-%d %H:%M:%S'
        )
        file_handler.setFormatter(file_format)
        self._logger.addHandler(file_handler)
        
        self._logger.info(f"日志系统初始化完成，日志目录: {self._log_dir}")
    
    @property
    def logger(self) -> logging.Logger:
        """获取logger实例"""
        if self._logger is None:
            self.setup()
        return self._logger
    
    def log_event(self, event_type: str, zone_id: str, message: str):
        """记录事件"""
        self.logger.info(f"[{event_type}] 灶台 {zone_id}: {message}")
    
    def log_warning(self, zone_id: str, message: str):
        """记录预警事件"""
        self.log_event("预警", zone_id, message)
    
    def log_cutoff(self, zone_id: str, message: str):
        """记录切电事件"""
        self.log_event("切电", zone_id, message)
    
    def log_reset(self, zone_id: str, message: str):
        """记录复位事件"""
        self.log_event("复位", zone_id, message)
    
    def save_snapshot(self, zone_id: str, frame: np.ndarray, event_type: str = "cutoff") -> Optional[str]:
        """
        保存告警截图
        
        Args:
            zone_id: 灶台ID
            frame: 图像帧
            event_type: 事件类型
        
        Returns:
            截图文件路径，失败返回None
        """
        if self._snapshot_dir is None:
            self.setup()
        
        try:
            timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
            filename = f"{zone_id}_{event_type}_{timestamp}.jpg"
            filepath = self._snapshot_dir / filename
            
            cv2.imwrite(str(filepath), frame)
            self.logger.info(f"截图已保存: {filepath}")
            return str(filepath)
        except Exception as e:
            self.logger.error(f"保存截图失败: {e}")
            return None
    
    def get_snapshots(self, zone_id: Optional[str] = None, limit: int = 10) -> list:
        """
        获取截图列表
        
        Args:
            zone_id: 灶台ID，为空时返回所有
            limit: 返回数量限制
        
        Returns:
            截图文件路径列表
        """
        if self._snapshot_dir is None:
            self.setup()
        
        snapshots = []
        for f in sorted(self._snapshot_dir.glob("*.jpg"), reverse=True):
            if zone_id is None or f.name.startswith(zone_id):
                snapshots.append({
                    'filename': f.name,
                    'path': str(f),
                    'timestamp': f.stat().st_mtime
                })
                if len(snapshots) >= limit:
                    break
        
        return snapshots


# 全局事件日志实例
event_logger = EventLogger()


def get_logger() -> logging.Logger:
    """获取全局logger"""
    return event_logger.logger
