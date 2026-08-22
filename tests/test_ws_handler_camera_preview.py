"""摄像头预览尺寸与质量参数回归测试。"""

from types import SimpleNamespace

import numpy as np

from src.api.ws_handler import WSHandler


async def test_ble_preview_downscales_long_edge_and_uses_requested_quality(monkeypatch):
    from src.camera.frame_cache import frame_cache
    from src.camera.manager import camera_manager

    frame = np.zeros((1080, 1920, 3), dtype=np.uint8)
    camera = SimpleNamespace(is_online=True, get_snapshot=lambda: frame)
    captured = {}

    monkeypatch.setattr(camera_manager, "get_camera", lambda camera_id: camera)

    def fake_get_or_encode(camera_id, encoded_frame, quality):
        captured.update(camera_id=camera_id, shape=encoded_frame.shape, quality=quality)
        return "encoded", False

    monkeypatch.setattr(frame_cache, "get_or_encode", fake_get_or_encode)

    result = await WSHandler()._preview_camera({
        "camera_id": "cam-1",
        "max_edge": 640,
        "quality": 60,
    })

    assert captured == {"camera_id": "cam-1", "shape": (360, 640, 3), "quality": 60}
    assert result == {"image": "data:image/jpeg;base64,encoded"}


async def test_web_preview_keeps_original_size_and_quality(monkeypatch):
    from src.camera.frame_cache import frame_cache
    from src.camera.manager import camera_manager

    frame = np.zeros((720, 1280, 3), dtype=np.uint8)
    camera = SimpleNamespace(is_online=True, get_snapshot=lambda: frame)
    captured = {}

    monkeypatch.setattr(camera_manager, "get_camera", lambda camera_id: camera)

    def fake_get_or_encode(camera_id, encoded_frame, quality):
        captured.update(camera_id=camera_id, shape=encoded_frame.shape, quality=quality)
        return "encoded", False

    monkeypatch.setattr(frame_cache, "get_or_encode", fake_get_or_encode)

    await WSHandler()._preview_camera({"camera_id": "cam-1"})

    assert captured == {"camera_id": "cam-1", "shape": (720, 1280, 3), "quality": 80}
