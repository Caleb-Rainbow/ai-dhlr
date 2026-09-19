"""硬件音量增益（get_audio_gain / set_audio_gain）协议契约测试。

RK809 播放链路 = 软件 volume × DAC(0-252) × HP Output Gain(0-3)。
DAC 上限是内核真实值 252（ALSA 虚报 255，写 253+ EINVAL）。
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


# ------------------------------ get_audio_gain ------------------------------ #
async def test_get_audio_gain_reads_both_controls(env, monkeypatch):
    handler, _, _ = env
    reads = {"DAC Playback Volume": 252, "HP Output Gain": 3}
    monkeypatch.setattr(
        WSHandler, "_amixer_read",
        lambda self, name: reads.get(name),
    )
    result = await handler._get_audio_gain({})
    assert result == {
        "supported": True,
        "dac": 252, "dac_max": 252,
        "hp_gain": 3, "hp_gain_max": 3,
    }


async def test_get_audio_gain_unsupported_returns_nulls(env, monkeypatch):
    """无 amixer（开发机/非 RK809 设备）→ supported=False，字段为 None。"""
    handler, _, _ = env
    monkeypatch.setattr(WSHandler, "_amixer_read", lambda self, name: None)
    result = await handler._get_audio_gain({})
    assert result["supported"] is False
    assert result["dac"] is None and result["hp_gain"] is None


# ------------------------------ set_audio_gain ------------------------------ #
async def test_set_audio_gain_writes_and_reads_back(env, monkeypatch):
    handler, _, _ = env
    state = {"DAC Playback Volume": 219, "HP Output Gain": 0}
    writes = []
    monkeypatch.setattr(
        WSHandler, "_amixer_read", lambda self, name: state.get(name),
    )
    def fake_write(self, name, value):
        writes.append((name, value))
        state[name] = value
        return True
    monkeypatch.setattr(WSHandler, "_amixer_write", fake_write)

    result = await handler._set_audio_gain({"dac": 252, "hp_gain": 3})
    assert writes == [("DAC Playback Volume", 252), ("HP Output Gain", 3)]
    assert result["supported"] is True
    assert result["dac"] == 252 and result["hp_gain"] == 3


async def test_set_audio_gain_partial_update(env, monkeypatch):
    """只传 hp_gain 时不动 DAC。"""
    handler, _, _ = env
    writes = []
    monkeypatch.setattr(WSHandler, "_amixer_read",
                        lambda self, name: {"DAC Playback Volume": 252, "HP Output Gain": 0}.get(name))
    monkeypatch.setattr(WSHandler, "_amixer_write",
                        lambda self, name, value: writes.append((name, value)) or True)

    result = await handler._set_audio_gain({"hp_gain": 2})
    assert writes == [("HP Output Gain", 2)]
    assert result["dac"] == 252  # DAC 未动


async def test_set_audio_gain_rejects_dac_over_kernel_max(env, monkeypatch):
    """内核上限 252：写 253（ALSA 虚报的 255 区间）必须被协议层拒绝。"""
    handler, _, _ = env
    monkeypatch.setattr(WSHandler, "_amixer_write", lambda self, name, value: True)
    with pytest.raises(ValueError, match="dac 越界"):
        await handler._set_audio_gain({"dac": 253})
    with pytest.raises(ValueError, match="dac 越界"):
        await handler._set_audio_gain({"dac": -1})


async def test_set_audio_gain_rejects_hp_out_of_range(env, monkeypatch):
    handler, _, _ = env
    monkeypatch.setattr(WSHandler, "_amixer_write", lambda self, name, value: True)
    with pytest.raises(ValueError, match="hp_gain 越界"):
        await handler._set_audio_gain({"hp_gain": 4})


async def test_set_audio_gain_write_failure_raises(env, monkeypatch):
    handler, _, _ = env
    monkeypatch.setattr(WSHandler, "_amixer_write", lambda self, name, value: False)
    with pytest.raises(ValueError, match="DAC 音量写入失败"):
        await handler._set_audio_gain({"dac": 200})


# ------------------------------ 原有 settings 行为回归 ------------------------------ #
async def test_get_settings_all_only_legacy_categories(env):
    """收窄后 get_settings(all) 只含 alarm/system/voice。"""
    handler, _, _ = env
    result = await handler._get_settings({"category": "all"})
    assert set(result) == {"alarm", "system", "voice"}


async def test_update_alarm_still_works(env):
    handler, cfg, saved = env
    result = await handler._update_settings({"category": "alarm", "settings": {
        "warning_time": 30,
    }})
    assert result == {"message": "设置已更新"}
    assert cfg.alarm.warning_time == 30
    assert saved
