"""
日志工具模块
提供统一的日志记录和事件保存功能
"""
import os
import time
import logging
import datetime
from pathlib import Path
from typing import Optional
import cv2
import numpy as np


class DailyFileHandler(logging.FileHandler):
    """
    按天自动轮转的日志处理器
    日志文件名格式: {prefix}_{YYYY-MM-DD}.log
    当日期变化时自动切换到新的日志文件
    """
    
    def __init__(self, log_dir: Path, prefix: str = "fire_safety", encoding: str = 'utf-8', retention_days: int = 7):
        self.log_dir = log_dir
        self.prefix = prefix
        self._encoding = encoding
        self._retention_days = retention_days
        self._current_date = datetime.date.today()

        # 初始化时使用当天日期的文件
        log_file = self._get_log_filename()
        super().__init__(log_file, encoding=encoding)

        # 启动时清理过期日志
        self._cleanup_old_logs()
    
    def _get_log_filename(self) -> str:
        """获取当前日期对应的日志文件名"""
        date_str = self._current_date.strftime("%Y-%m-%d")
        return str(self.log_dir / f"{self.prefix}_{date_str}.log")
    
    def _check_and_rotate(self):
        """检查是否需要切换到新的日志文件"""
        today = datetime.date.today()
        if today != self._current_date:
            # 日期变了，切换到新文件
            self._current_date = today

            # 关闭当前文件
            if self.stream:
                self.stream.close()
                self.stream = None

            # 更新文件名并重新打开
            self.baseFilename = self._get_log_filename()
            self.stream = self._open()

            # 每天清理过期日志
            self._cleanup_old_logs()

    def _cleanup_old_logs(self):
        """删除超过保留天数的日志文件"""
        if self._retention_days <= 0:
            return
        cutoff_date = datetime.date.today() - datetime.timedelta(days=self._retention_days)
        # 文件名格式: {prefix}_{YYYY-MM-DD}.log；prefix 可能含下划线（如 fire_safety），
        # 故按已知 prefix 长度截取日期，勿用 split('_')——旧实现把 "fire_safety" 拆成两段
        # 导致 date_str="safety_2026-01-13" 永远 fromisoformat 失败 → 历史日志从不被清理。
        prefix_len = len(self.prefix) + 1  # prefix + '_'
        for f in self.log_dir.glob(f"{self.prefix}_*.log"):
            try:
                date_str = f.stem[prefix_len:]
                file_date = datetime.date.fromisoformat(date_str)
                if file_date < cutoff_date:
                    f.unlink()
            except (ValueError, IndexError):
                pass
    
    def emit(self, record):
        """写入日志前检查是否需要轮转"""
        self._check_and_rotate()
        super().emit(record)


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
        self._log_retention_days = 7
        self._snapshot_retention_days = 3
        self._last_snapshot_cleanup = 0.0

    def setup(self, level: str = "INFO", log_dir: str = "logs", snapshot_dir: str = "snapshots",
              log_retention_days: int = 7, snapshot_retention_days: int = 3):
        """初始化日志配置"""
        self._log_retention_days = max(0, int(log_retention_days))
        self._snapshot_retention_days = max(0, int(snapshot_retention_days))

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

        # 文件handler - 使用 DailyFileHandler 实现日志按天自动轮转
        # 日志文件名格式: fire_safety_2026-01-07.log
        file_handler = DailyFileHandler(
            self._log_dir, prefix="fire_safety", encoding='utf-8',
            retention_days=self._log_retention_days,
        )
        file_handler.setLevel(log_level)
        file_format = logging.Formatter(
            '%(asctime)s [%(levelname)s] [%(filename)s:%(lineno)d] %(message)s',
            datefmt='%Y-%m-%d %H:%M:%S'
        )
        file_handler.setFormatter(file_format)
        self._logger.addHandler(file_handler)

        # 启动时清理过期快照（运行期再由 save_snapshot 节流清理）
        self._cleanup_old_snapshots()

        self._logger.info(f"日志系统初始化完成，日志目录: {self._log_dir}")

    def _cleanup_old_snapshots(self):
        """删除超过保留天数的告警快照（按文件 mtime，避免依赖文件名格式）"""
        if self._snapshot_retention_days <= 0 or self._snapshot_dir is None:
            return
        cutoff = time.time() - self._snapshot_retention_days * 86400
        removed = 0
        try:
            for f in self._snapshot_dir.glob("*.jpg"):
                try:
                    if f.stat().st_mtime < cutoff:
                        f.unlink()
                        removed += 1
                except Exception:
                    pass
            if removed > 0:
                self.logger.info(f"清理过期告警快照 {removed} 个（保留 {self._snapshot_retention_days} 天）")
        except Exception as e:
            self.logger.warning(f"清理告警快照失败: {e}")
    
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

        # 节流清理过期快照（每小时最多一次，避免每次告警都遍历目录）
        now = time.time()
        if now - self._last_snapshot_cleanup > 3600:
            self._last_snapshot_cleanup = now
            try:
                self._cleanup_old_snapshots()
            except Exception:
                pass

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
