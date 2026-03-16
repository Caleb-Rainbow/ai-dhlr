"""
摄像头帧缓存管理器
用于减少多客户端预览时的重复编码开销
"""
import time
import threading
import base64
from typing import Optional, Dict, Tuple
from dataclasses import dataclass

from ..utils.logger import get_logger

logger = get_logger()


@dataclass
class CachedFrame:
    """缓存的帧数据"""
    jpeg_bytes: bytes  # JPEG 编码后的原始字节
    base64_str: str    # Base64 编码字符串
    timestamp: float   # 缓存时间戳
    quality: int       # JPEG 质量


class FrameCache:
    """
    帧缓存管理器

    在 TTL 时间内复用已编码的帧，减少 CPU 编码开销。
    适合 RK3566 等边缘设备的多客户端预览场景。

    使用示例:
        cache = FrameCache(ttl_ms=500)

        # 获取或编码帧
        result = cache.get_or_encode(camera_id, frame, quality=80)
        if result:
            base64_str = result[0]
            is_from_cache = result[1]
    """

    def __init__(self, ttl_ms: int = 500):
        """
        初始化帧缓存

        Args:
            ttl_ms: 缓存有效期（毫秒），默认500ms
        """
        self._cache: Dict[str, CachedFrame] = {}
        self._lock = threading.Lock()
        self._ttl_ms = ttl_ms
        self._hits = 0
        self._misses = 0

    def get_or_encode(self, camera_id: str, frame, quality: int = 80) -> Optional[Tuple[str, bool]]:
        """
        获取缓存的帧或编码新帧

        Args:
            camera_id: 摄像头ID
            frame: BGR 格式的帧数据 (numpy array)
            quality: JPEG 编码质量 (1-100)

        Returns:
            (base64_str, is_from_cache) 元组，失败返回 None
        """
        if frame is None:
            return None

        current_time = time.time()
        cache_key = f"{camera_id}_{quality}"

        # 尝试从缓存获取
        with self._lock:
            cached = self._cache.get(cache_key)
            if cached:
                age_ms = (current_time - cached.timestamp) * 1000
                if age_ms < self._ttl_ms:
                    self._hits += 1
                    logger.debug(f"帧缓存命中: {camera_id}, age={age_ms:.0f}ms")
                    return (cached.base64_str, True)
                else:
                    # 缓存过期，移除
                    del self._cache[cache_key]

            self._misses += 1

        # 编码新帧
        jpeg_bytes, base64_str = self._encode_frame(frame, quality)
        if jpeg_bytes is None:
            return None

        # 存入缓存
        with self._lock:
            self._cache[cache_key] = CachedFrame(
                jpeg_bytes=jpeg_bytes,
                base64_str=base64_str,
                timestamp=current_time,
                quality=quality
            )

        return (base64_str, False)

    def _encode_frame(self, frame, quality: int) -> Tuple[Optional[bytes], Optional[str]]:
        """
        编码帧为 JPEG + Base64

        Args:
            frame: BGR 格式的帧数据
            quality: JPEG 编码质量

        Returns:
            (jpeg_bytes, base64_str) 元组，失败返回 (None, None)
        """
        import cv2

        encode_param = [int(cv2.IMWRITE_JPEG_QUALITY), quality]
        ret, jpeg = cv2.imencode('.jpg', frame, encode_param)

        if not ret:
            logger.warning(f"帧编码失败: quality={quality}")
            return None, None

        jpeg_bytes = jpeg.tobytes()
        base64_str = base64.b64encode(jpeg_bytes).decode('utf-8')

        return jpeg_bytes, base64_str

    def invalidate(self, camera_id: str = None):
        """
        使缓存失效

        Args:
            camera_id: 指定摄像头ID，为 None 时清除所有缓存
        """
        with self._lock:
            if camera_id:
                # 移除指定摄像头的所有缓存
                keys_to_remove = [k for k in self._cache if k.startswith(f"{camera_id}_")]
                for key in keys_to_remove:
                    del self._cache[key]
            else:
                # 清空所有缓存
                self._cache.clear()

    def get_stats(self) -> dict:
        """获取缓存统计信息"""
        with self._lock:
            total = self._hits + self._misses
            hit_rate = (self._hits / total * 100) if total > 0 else 0
            return {
                "hits": self._hits,
                "misses": self._misses,
                "hit_rate": f"{hit_rate:.1f}%",
                "cached_items": len(self._cache),
                "ttl_ms": self._ttl_ms
            }

    def cleanup_expired(self):
        """清理过期的缓存项"""
        current_time = time.time()
        with self._lock:
            expired_keys = [
                k for k, v in self._cache.items()
                if (current_time - v.timestamp) * 1000 >= self._ttl_ms
            ]
            for key in expired_keys:
                del self._cache[key]

            if expired_keys:
                logger.debug(f"清理了 {len(expired_keys)} 个过期缓存项")


# 全局帧缓存实例
frame_cache = FrameCache(ttl_ms=500)
