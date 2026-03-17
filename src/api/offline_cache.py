"""
离线消息缓存模块
用于断网时缓存报警记录，恢复后自动补发
"""
import threading
import time
import json
from collections import deque
from pathlib import Path
from typing import Optional, List, Dict, Any

from ..utils.logger import get_logger

logger = get_logger()


class OfflineCache:
    """
    离线消息缓存队列

    - 内存缓存 + 文件持久化
    - 断网时缓存报警记录
    - 恢复后自动补发
    """

    def __init__(self, cache_dir: str = "cache", max_size: int = 100):
        """
        初始化离线缓存

        Args:
            cache_dir: 缓存目录
            max_size: 最大缓存数量
        """
        self._queue: deque = deque(maxlen=max_size)
        self._lock = threading.Lock()
        self._cache_dir = Path(cache_dir)
        self._cache_file = self._cache_dir / "offline_records.json"
        self._max_size = max_size
        self._enabled = True

        # 启动时加载持久化缓存
        self._load_from_file()

    def set_enabled(self, enabled: bool):
        """设置缓存是否启用"""
        self._enabled = enabled

    @property
    def enabled(self) -> bool:
        return self._enabled

    def push(self, message: Dict[str, Any]) -> bool:
        """
        缓存消息

        Args:
            message: 消息内容

        Returns:
            是否成功缓存
        """
        if not self._enabled:
            logger.debug("离线缓存未启用，跳过缓存")
            return False

        with self._lock:
            if len(self._queue) >= self._max_size:
                logger.warning(f"离线缓存已满({self._max_size})，丢弃最旧记录")

            record = {
                "message": message,
                "timestamp": time.time(),
                "retry_count": 0
            }
            self._queue.append(record)
            self._save_to_file()
            logger.info(f"报警记录已缓存（当前缓存: {len(self._queue)} 条）")
            return True

    def pop_all(self) -> List[Dict[str, Any]]:
        """
        取出所有缓存消息

        Returns:
            所有缓存的消息列表
        """
        with self._lock:
            messages = [item["message"] for item in self._queue]
            self._queue.clear()
            self._save_to_file()
            return messages

    def peek_all(self) -> List[Dict[str, Any]]:
        """
        查看所有缓存消息（不移除）

        Returns:
            所有缓存的消息列表
        """
        with self._lock:
            return [item["message"] for item in list(self._queue)]

    def push_back(self, messages: List[Dict[str, Any]]) -> int:
        """
        将消息重新放回缓存（补发失败时使用）

        Args:
            messages: 需要重新缓存的消息列表

        Returns:
            成功放回的消息数量
        """
        if not messages:
            return 0

        with self._lock:
            count = 0
            for msg in messages:
                if len(self._queue) >= self._max_size:
                    logger.warning(f"离线缓存已满，无法放回更多消息")
                    break
                record = {
                    "message": msg,
                    "timestamp": time.time(),
                    "retry_count": 1  # 标记为重试消息
                }
                self._queue.append(record)
                count += 1
            self._save_to_file()
            return count

    def _save_to_file(self):
        """持久化到文件"""
        try:
            self._cache_dir.mkdir(parents=True, exist_ok=True)
            with open(self._cache_file, 'w', encoding='utf-8') as f:
                json.dump(list(self._queue), f, ensure_ascii=False, indent=2)
        except Exception as e:
            logger.error(f"保存离线缓存失败: {e}")

    def _load_from_file(self):
        """从文件加载缓存"""
        try:
            if self._cache_file.exists():
                with open(self._cache_file, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                    for item in data[-self._max_size:]:
                        self._queue.append(item)
                if self._queue:
                    logger.info(f"加载离线缓存 {len(self._queue)} 条")
        except Exception as e:
            logger.error(f"加载离线缓存失败: {e}")

    def clear(self):
        """清空缓存"""
        with self._lock:
            self._queue.clear()
            self._save_to_file()

    @property
    def size(self) -> int:
        """当前缓存数量"""
        return len(self._queue)

    @property
    def is_empty(self) -> bool:
        """缓存是否为空"""
        return len(self._queue) == 0


# 全局实例
offline_cache = OfflineCache()
