"""硬件音量增益（get_audio_gain / set_audio_gain）协议契约 + 开机恢复测试。

RK809 播放链路 = 软件 volume × DAC(0-252) × HP Output Gain(0-3)。
DAC 上限是内核真实值 252（ALSA 虚报 255，写 253+ EINVAL）。
用户设置经 set_audio_gain 持久化到 config.voice.hw_dac/hw_hp_gain，
开机由 utils/audio_gain.restore_configured 重放（对抗 WirePlumber 拉回 ~86%）。
"""
import pytest

from src.api.ws_handler import WSHandler
from src.utils import audio_gain
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
    monkeypatch.setattr(
        audio_gain, "read_gain",
        lambda: {"supported": True, "dac": 252, "dac_max": 252, "hp_gain": 3, "hp_gain_max": 3},
    )
    result = await handler._get_audio_gain({})
    assert result == {
        "supported": True,
        "dac": 252, "dac_max": 252,
        "hp_gain": 3, "hp_gain_max": 3,
    }


async def test_get_audio_gain_unsupported_returns_nulls(env, monkeypatch):
    handler, _, _ = env
    monkeypatch.setattr(audio_gain, "read_gain",
                        lambda: {"supported": False, "dac": None, "dac_max": None,
                                 "hp_gain": None, "hp_gain_max": None})
    result = await handler._get_audio_gain({})
    assert result["supported"] is False
    assert result["dac"] is None and result["hp_gain"] is None


# ------------------------------ set_audio_gain ------------------------------ #
async def test_set_audio_gain_writes_and_persists(env, monkeypatch):
    """写入成功后，用户设置必须落 config（供开机恢复）并 save。"""
    handler, cfg, saved = env
    calls = []

    def fake_write_gain(dac=None, hp_gain=None):
        calls.append((dac, hp_gain))
        return {"supported": True, "dac": dac, "dac_max": 252, "hp_gain": hp_gain, "hp_gain_max": 3}

    monkeypatch.setattr(audio_gain, "write_gain", fake_write_gain)

    result = await handler._set_audio_gain({"dac": 252, "hp_gain": 3})
    assert calls == [(252, 3)]
    assert cfg.voice.hw_dac == 252
    assert cfg.voice.hw_hp_gain == 3
    assert saved  # 已落盘
    assert result["dac"] == 252 and result["hp_gain"] == 3


async def test_set_audio_gain_partial_persists_only_written(env, monkeypatch):
    """只传 hp_gain 时不动 DAC，也只持久化 hp_gain。"""
    handler, cfg, _ = env
    monkeypatch.setattr(audio_gain, "write_gain",
                        lambda dac=None, hp_gain=None: {"supported": True, "dac": dac, "dac_max": 252,
                                                        "hp_gain": hp_gain, "hp_gain_max": 3})
    await handler._set_audio_gain({"hp_gain": 2})
    assert cfg.voice.hw_dac is None
    assert cfg.voice.hw_hp_gain == 2


async def test_set_audio_gain_rejects_dac_over_kernel_max(env, monkeypatch):
    """内核上限 252：写 253（ALSA 虚报的 255 区间）必须被拒绝，且不持久化。"""
    handler, cfg, saved = env

    def bad_write_gain(dac=None, hp_gain=None):
        raise ValueError("dac 越界，允许 0..252（内核上限，非 ALSA 虚报的 255）")

    monkeypatch.setattr(audio_gain, "write_gain", bad_write_gain)
    with pytest.raises(ValueError, match="dac 越界"):
        await handler._set_audio_gain({"dac": 253})
    assert cfg.voice.hw_dac is None
    assert not saved


# ------------------------------ write_gain 底层校验 ------------------------------ #
def test_write_gain_validates_bounds(monkeypatch):
    monkeypatch.setattr(audio_gain, "write_control", lambda name, value: True)
    monkeypatch.setattr(audio_gain, "read_gain",
                        lambda: {"supported": True, "dac": 0, "dac_max": 252, "hp_gain": 0, "hp_gain_max": 3})
    with pytest.raises(ValueError, match="dac 越界"):
        audio_gain.write_gain(dac=253)
    with pytest.raises(ValueError, match="hp_gain 越界"):
        audio_gain.write_gain(hp_gain=4)
    with pytest.raises(ValueError, match="dac 必须是整数"):
        audio_gain.write_gain(dac="abc")


def test_write_gain_write_failure_raises(monkeypatch):
    monkeypatch.setattr(audio_gain, "write_control", lambda name, value: False)
    with pytest.raises(ValueError, match="DAC 音量写入失败"):
        audio_gain.write_gain(dac=200)


# ------------------------------ 开机恢复 ------------------------------ #
def test_restore_replays_configured_gain(monkeypatch):
    """WirePlumber 先拉回 86%：第一轮发现 DAC=219 与配置 252 不符 → 重写并复核通过。"""
    state = {"DAC Playback Volume": 219, "HP Output Gain": 3}
    monkeypatch.setattr(audio_gain, "_has_amixer", lambda: True)
    monkeypatch.setattr(audio_gain, "read_control", lambda name: state.get(name))
    writes = []

    def fake_write(name, value):
        writes.append((name, value))
        state[name] = value
        return True

    monkeypatch.setattr(audio_gain, "write_control", fake_write)
    monkeypatch.setattr(config_manager, "_config", _build_config())
    config_manager.config.voice.hw_dac = 252
    config_manager.config.voice.hw_hp_gain = 3

    assert audio_gain.restore_configured(retries=2, interval=0) is True
    assert ("DAC Playback Volume", 252) in writes
    assert ("HP Output Gain", 3) not in writes  # HP 本来就符合，不重写


def test_restore_retries_until_wireplumber_stops_overriding(monkeypatch):
    """模拟 WirePlumber 晚启动：前两轮写入后被改回，第三轮稳定 → 重试机制生效。"""
    state = {"DAC Playback Volume": 219, "HP Output Gain": 3}
    override_left = {"n": 2}  # 前两轮的「写后复核」阶段，模拟外部又把 DAC 拉回 86%

    def fake_write(name, value):
        state[name] = value
        return True

    def fake_read(name):
        v = state.get(name)
        if name == "DAC Playback Volume" and v == 252 and override_left["n"] > 0:
            override_left["n"] -= 1
            return 219
        return v

    monkeypatch.setattr(audio_gain, "_has_amixer", lambda: True)
    monkeypatch.setattr(audio_gain, "write_control", fake_write)
    monkeypatch.setattr(audio_gain, "read_control", fake_read)
    monkeypatch.setattr(config_manager, "_config", _build_config())
    config_manager.config.voice.hw_dac = 252
    config_manager.config.voice.hw_hp_gain = None

    assert audio_gain.restore_configured(retries=5, interval=0) is True


def test_restore_noop_when_never_configured(monkeypatch):
    """用户从未设置（None）→ 不写任何控件，不与 WirePlumber 抢。"""
    monkeypatch.setattr(audio_gain, "_has_amixer", lambda: True)
    writes = []
    monkeypatch.setattr(audio_gain, "write_control",
                        lambda name, value: writes.append((name, value)) or True)
    monkeypatch.setattr(config_manager, "_config", _build_config())

    assert audio_gain.restore_configured(retries=1, interval=0) is True
    assert writes == []


def test_restore_without_amixer_returns_false(monkeypatch):
    monkeypatch.setattr(audio_gain, "_has_amixer", lambda: False)
    assert audio_gain.restore_configured(retries=1, interval=0) is False


# ------------------------------ 原有 settings 行为回归 ------------------------------ #
async def test_get_settings_all_only_legacy_categories(env):
    """get_settings(all) 只含 alarm/system/voice。"""
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
