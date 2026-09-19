"""get_settings / update_settings 参数类目协议契约测试。

覆盖新增类目：inference / detection / logging / disk_guard / discovery / voice，
以及边界校验与 restart_required 回传语义。
"""
import pytest

from src.api.ws_handler import WSHandler
from src.utils.config import (
    AppConfig,
    ApiConfig,
    DetectionConfig,
    GpioConfig,
    InferenceConfig,
    LoggingConfig,
    SystemConfig,
    VoiceConfig,
    config_manager,
)


def _build_config() -> AppConfig:
    return AppConfig(
        system=SystemConfig(),
        inference=InferenceConfig(),
        detection=DetectionConfig(),
        cameras=[],
        zones=[],
        api=ApiConfig(),
        voice=VoiceConfig(),
        logging=LoggingConfig(),
        gpio=GpioConfig(),
    )


@pytest.fixture
def env(monkeypatch):
    """注入隔离的真实 AppConfig；save 打桩避免写盘。"""
    cfg = _build_config()
    saved = []
    monkeypatch.setattr(config_manager, "_config", cfg)
    monkeypatch.setattr(config_manager, "save", lambda: saved.append(True))
    return WSHandler(), cfg, saved


# ------------------------------ get_settings ------------------------------ #
async def test_get_settings_all_returns_new_categories(env):
    handler, cfg, _ = env
    result = await handler._get_settings({"category": "all"})

    assert set(result) == {
        "alarm", "system", "voice", "inference", "detection",
        "logging", "disk_guard", "discovery",
    }
    assert result["inference"]["confidence_threshold"] == cfg.inference.confidence_threshold
    assert result["detection"]["no_person_threshold"] == cfg.detection.no_person_threshold
    assert result["logging"]["level"] == cfg.logging.level
    assert result["disk_guard"]["warn_usage_pct"] == cfg.disk_guard.warn_usage_pct
    assert result["discovery"]["announce_interval"] == cfg.discovery.announce_interval


async def test_get_settings_single_category(env):
    handler, _, _ = env
    result = await handler._get_settings({"category": "inference"})
    assert set(result) == {"inference"}


# ------------------------------ inference ------------------------------ #
async def test_update_inference_accepts_valid_and_flags_restart(env):
    handler, cfg, saved = env
    result = await handler._update_settings({"category": "inference", "settings": {
        "confidence_threshold": 0.6,
        "model_path": "yolov11s-sim.rknn",
        "engine": "rknn",
    }})
    assert result["restart_required"] is True
    assert cfg.inference.confidence_threshold == 0.6
    assert cfg.inference.model_path == "yolov11s-sim.rknn"
    assert saved  # 已落盘


@pytest.mark.parametrize("settings,match", [
    ({"confidence_threshold": 0}, "confidence_threshold 越界"),
    ({"confidence_threshold": 1.5}, "confidence_threshold 越界"),
    ({"engine": "onnx"}, "engine"),
    ({"model_path": "../evil.rknn"}, "model_path"),
    ({"model_path": "sub/dir/model.rknn"}, "model_path"),
    ({"model_path": "model.txt"}, "model_path"),
])
async def test_update_inference_rejects_invalid(env, settings, match):
    handler, cfg, saved = env
    with pytest.raises(ValueError, match=match):
        await handler._update_settings({"category": "inference", "settings": settings})
    assert not saved  # 校验失败不应落盘


# ------------------------------ detection ------------------------------ #
async def test_update_detection_bounds(env):
    handler, cfg, _ = env
    await handler._update_settings({"category": "detection", "settings": {
        "no_person_threshold": 5, "person_present_threshold": 3,
    }})
    assert cfg.detection.no_person_threshold == 5
    assert cfg.detection.person_present_threshold == 3

    with pytest.raises(ValueError, match="no_person_threshold 越界"):
        await handler._update_settings({"category": "detection",
                                        "settings": {"no_person_threshold": 0}})
    with pytest.raises(ValueError, match="person_present_threshold 必须是整数"):
        await handler._update_settings({"category": "detection",
                                        "settings": {"person_present_threshold": "abc"}})


# ------------------------------ logging ------------------------------ #
async def test_update_logging_valid_and_level_check(env):
    handler, cfg, _ = env
    await handler._update_settings({"category": "logging", "settings": {
        "level": "debug",  # 大小写归一
        "log_retention_days": 30,
        "snapshot_retention_days": 0,
    }})
    assert cfg.logging.level == "DEBUG"
    assert cfg.logging.log_retention_days == 30
    assert cfg.logging.snapshot_retention_days == 0

    with pytest.raises(ValueError, match="level"):
        await handler._update_settings({"category": "logging",
                                        "settings": {"level": "VERBOSE"}})


# ------------------------------ disk_guard ------------------------------ #
async def test_update_disk_guard_cross_validation(env):
    handler, cfg, _ = env
    await handler._update_settings({"category": "disk_guard", "settings": {
        "warn_usage_pct": 80, "critical_usage_pct": 90,
    }})
    assert cfg.disk_guard.warn_usage_pct == 80

    # critical <= warn 应被拒绝
    with pytest.raises(ValueError, match="critical_usage_pct"):
        await handler._update_settings({"category": "disk_guard", "settings": {
            "critical_usage_pct": 80,
        }})


# ------------------------------ discovery ------------------------------ #
async def test_update_discovery_interval_bounds(env):
    handler, cfg, _ = env
    await handler._update_settings({"category": "discovery", "settings": {
        "enabled": False, "announce_interval": 30,
    }})
    assert cfg.discovery.enabled is False
    assert cfg.discovery.announce_interval == 30

    with pytest.raises(ValueError, match="announce_interval 越界"):
        await handler._update_settings({"category": "discovery",
                                        "settings": {"announce_interval": 9999}})


# ------------------------------ voice ------------------------------ #
async def test_update_voice_runtime_toggle(env, monkeypatch):
    handler, cfg, _ = env
    from src.output.voice import voice_player

    calls = []
    monkeypatch.setattr(voice_player, "set_enabled", lambda enabled: calls.append(enabled))

    result = await handler._update_settings({"category": "voice", "settings": {"enabled": False}})
    assert result["restart_required"] is False  # voice 运行时生效
    assert cfg.voice.enabled is False
    assert calls == [False]


# ------------------------------ 兼容与加固 ------------------------------ #
async def test_update_alarm_still_works_without_restart_flag(env):
    handler, cfg, _ = env
    result = await handler._update_settings({"category": "alarm", "settings": {
        "warning_time": 30,
    }})
    assert result["restart_required"] is False
    assert cfg.alarm.warning_time == 30


async def test_update_unknown_category_rejected(env):
    handler, _, _ = env
    with pytest.raises(ValueError, match="未知的设置类目"):
        await handler._update_settings({"category": "nope", "settings": {}})
