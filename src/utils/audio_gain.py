# -*- coding: utf-8 -*-
"""RK809 硬件音量增益：amixer 读写 + 开机恢复。

播放链路 = 软件 volume（pygame set_volume）× DAC Playback Volume × HP Output Gain，
后两级为 codec 硬件增益，经 amixer 直读直写、即时生效。

开机恢复的必要性：PipeWire/WirePlumber 启动时会把它管理的 sink 音量（默认 1.0）
按自身映射写回 codec（DAC ≈ 219/252 ≈ 86%），覆盖寄存器里的上次设置——且 sink 音量
>1.15 时映射值超过内核真实上限 252 会全部写失败。因此用户在 UI/BLE 设置的增益
必须持久化到 config.voice.hw_dac/hw_hp_gain，开机后由应用重放（见 [restore_configured]）。
"""
import shutil
import subprocess
import threading
import time
from typing import Optional

from .logger import get_logger

logger = get_logger()

# 内核真实上限：DAC 252（ALSA 虚报 255，写 253+ 报 EINVAL）、HP 增益 3
DAC_MAX = 252
HP_GAIN_MAX = 3

_CARD = "0"
_DAC_CONTROL = "DAC Playback Volume"
_HP_CONTROL = "HP Output Gain"
settings_lock = threading.RLock()


def _has_amixer() -> bool:
    return shutil.which("amixer") is not None


def read_control(name: str) -> Optional[int]:
    """读 ALSA 控件当前值（取第一个声道）。无 amixer / 控件不存在返回 None。"""
    if not _has_amixer():
        return None
    try:
        out = subprocess.run(
            ["amixer", "-c", _CARD, "cget", f"name={name}"],
            capture_output=True, text=True, timeout=3,
        ).stdout
        for line in out.splitlines():
            if ": values=" in line:
                return int(line.split(": values=")[1].split(",")[0].strip())
    except Exception:
        pass
    return None


def write_control(name: str, value: int) -> bool:
    """写 ALSA 控件（双声道控件写两份）。失败返回 False。"""
    if not _has_amixer():
        return False
    try:
        # 双声道控件（DAC）需同时写两份值；HP 增益为 volume-joined 单值
        value_arg = f"{value},{value}" if name == _DAC_CONTROL else str(value)
        r = subprocess.run(
            ["amixer", "-c", _CARD, "cset", f"name={name}", value_arg],
            capture_output=True, text=True, timeout=10,
        )
        if r.returncode != 0:
            logger.warning(f"amixer cset 失败: {name}={value_arg} rc={r.returncode} stderr={r.stderr.strip()[:120]}")
            return False
        return True
    except Exception as e:
        logger.warning(f"amixer cset 异常: {name}={value}: {e}")
        return False


def read_gain() -> dict:
    """读取两级硬件增益（get_audio_gain action 的应答体）。"""
    dac = read_control(_DAC_CONTROL)
    hp_gain = read_control(_HP_CONTROL)
    return {
        "supported": dac is not None or hp_gain is not None,
        "dac": dac,
        "dac_max": DAC_MAX if dac is not None else None,
        "hp_gain": hp_gain,
        "hp_gain_max": HP_GAIN_MAX if hp_gain is not None else None,
    }


def write_gain(dac=None, hp_gain=None) -> dict:
    """设置硬件增益（set_audio_gain action 的实现），即时生效并回读。

    抛 ValueError：参数缺失/非法/越界/写入失败。dac/hp_gain 可只传其一。
    """
    if dac is not None:
        try:
            dac = int(dac)
        except (TypeError, ValueError):
            raise ValueError("dac 必须是整数")
        if not (0 <= dac <= DAC_MAX):
            raise ValueError(f"dac 越界，允许 0..{DAC_MAX}（内核上限，非 ALSA 虚报的 255）")
        if not write_control(_DAC_CONTROL, dac):
            raise ValueError("DAC 音量写入失败（设备无 amixer 或控件不可用）")

    if hp_gain is not None:
        try:
            hp_gain = int(hp_gain)
        except (TypeError, ValueError):
            raise ValueError("hp_gain 必须是整数")
        if not (0 <= hp_gain <= HP_GAIN_MAX):
            raise ValueError(f"hp_gain 越界，允许 0..{HP_GAIN_MAX}")
        if not write_control(_HP_CONTROL, hp_gain):
            raise ValueError("HP 增益写入失败（设备无 amixer 或控件不可用）")

    return read_gain()


def restore_configured(retries: int = 5, interval: float = 10.0,
                       stop_event: Optional[threading.Event] = None) -> bool:
    """把 config.voice.hw_dac/hw_hp_gain 重放到 codec（开机恢复入口）。

    - 用户从未设置过（两者皆 None）→ 不干预，保持 WirePlumber/系统默认
    - 带 retries 重试：应用与 WirePlumber 的启动顺序不保证，WirePlumber 晚启动会
      覆盖我们的写入，因此必须完成整个复核窗口，不能首次匹配就提前退出。
    - 每轮读取最新配置，并与用户设置互斥，避免重放过期设置。
    返回最终是否与配置一致。
    """
    if not _has_amixer():
        return False
    from .config import config_manager
    matched = False
    changed = False
    for attempt in range(retries):
        if stop_event is not None and stop_event.is_set():
            return False
        with settings_lock:
            voice = config_manager.config.voice
            dac, hp = voice.hw_dac, voice.hw_hp_gain
            if dac is None and hp is None:
                return True
            current = read_gain()
            if dac is not None and current["dac"] != dac:
                changed = True
                write_control(_DAC_CONTROL, dac)
            if hp is not None and current["hp_gain"] != hp:
                changed = True
                write_control(_HP_CONTROL, hp)
            after = read_gain()
            matched = ((dac is None or after["dac"] == dac)
                       and (hp is None or after["hp_gain"] == hp))
        if not matched:
            logger.warning(
                f"硬件音量增益恢复未就绪（第 {attempt + 1}/{retries} 次，"
                f"当前 DAC={after['dac']} HP={after['hp_gain']}，目标 DAC={dac} HP={hp}）"
            )
        if attempt + 1 < retries:
            if stop_event is None:
                time.sleep(interval)
            elif stop_event.wait(interval):
                return False
    if matched:
        if changed or retries > 1:
            logger.info(f"硬件音量增益复核完成：DAC={after['dac']} HP={after['hp_gain']}")
        return True
    logger.error("硬件音量增益恢复失败：重试耗尽（检查音频服务状态）")
    return False


def start_restore_thread(stop_event: Optional[threading.Event] = None,
                         monitor_interval: float = 60.0) -> threading.Thread:
    """开机完整复核，之后定期恢复已保存的用户设置；主服务退出时停止。"""
    if stop_event is None:
        stop_event = threading.Event()

    def _worker():
        first = True
        while not stop_event.is_set():
            try:
                restore_configured(retries=5 if first else 1, stop_event=stop_event)
            except Exception as e:  # noqa: BLE001
                logger.error(f"硬件音量增益恢复线程异常: {e}")
            first = False
            if stop_event.wait(monitor_interval):
                return

    t = threading.Thread(target=_worker, daemon=True, name="audio-gain-restore")
    t.start()
    return t
