"""摄像头帧缓存键回归测试。"""

import base64

import numpy as np

from src.camera.frame_cache import FrameCache


def test_cache_separates_different_preview_dimensions(monkeypatch):
    cache = FrameCache(ttl_ms=1000)
    encode_calls = []

    def fake_encode(frame, quality):
        marker = f"{frame.shape[1]}x{frame.shape[0]}-q{quality}".encode()
        encode_calls.append(marker)
        return marker, base64.b64encode(marker).decode()

    monkeypatch.setattr(cache, "_encode_frame", fake_encode)

    large = np.zeros((720, 1280, 3), dtype=np.uint8)
    small = np.zeros((360, 640, 3), dtype=np.uint8)

    large_result = cache.get_or_encode("cam-1", large, quality=60)
    small_result = cache.get_or_encode("cam-1", small, quality=60)
    repeated_small_result = cache.get_or_encode("cam-1", small, quality=60)

    assert large_result == (base64.b64encode(encode_calls[0]).decode(), False)
    assert small_result == (base64.b64encode(encode_calls[1]).decode(), False)
    assert repeated_small_result == (small_result[0], True)
    assert len(encode_calls) == 2
