"""语音故障恢复回归：不访问真实声卡，子进程测试只启动受控的 Python。"""
import subprocess
import sys
import threading
import time
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from src.output import voice


AUDIO = str(Path(__file__).resolve().parents[1] / "audio_assets/no_zone/alarm.wav")


@pytest.fixture
def pygame(monkeypatch):
    module = MagicMock()
    module.mixer.get_init.return_value = (44100, -16, 2)
    module.mixer.music.get_busy.return_value = False
    monkeypatch.setitem(sys.modules, "pygame", module)
    return module


@pytest.fixture
def player(pygame):
    # 不修改应用全局单例，避免影响其他测试。
    instance = object.__new__(voice.VoicePlayer)
    instance.__init__()
    instance._logger = MagicMock()
    yield instance
    instance.stop()


def wait_until(predicate, timeout=2):
    deadline = time.monotonic() + timeout
    while not predicate():
        assert time.monotonic() < deadline, "播放器未在期限内完成"
        time.sleep(0.005)


def test_transient_init_failure_recovers_on_next_alarm(player, pygame, monkeypatch):
    pygame.mixer.init.side_effect = [RuntimeError("device busy"), None]
    fallback = MagicMock()
    monkeypatch.setattr(player, "_play_with_system_command", fallback)
    player._play_audio_file(AUDIO, threading.Event())
    fallback.assert_called_once()
    assert not player._pygame_initialized

    player._play_audio_file(AUDIO, threading.Event())
    assert pygame.mixer.init.call_count == 2
    pygame.mixer.music.play.assert_called_once()
    assert player._pygame_initialized


def test_runtime_failure_releases_device_before_retry(player, pygame):
    calls = []
    pygame.mixer.init.side_effect = lambda: calls.append("init")
    pygame.mixer.quit.side_effect = lambda: calls.append("quit")
    pygame.mixer.music.load.side_effect = [RuntimeError("device lost"), None]
    player._play_audio_file(AUDIO, threading.Event())
    assert calls == ["init", "quit", "init"]
    pygame.mixer.music.play.assert_called_once()


def test_repeated_failure_releases_mixer_before_fallback(player, pygame, monkeypatch):
    pygame.mixer.music.load.side_effect = [RuntimeError("lost"), RuntimeError("lost"), None]
    fallback = MagicMock(side_effect=lambda *args: (
        not player._pygame_initialized and pygame.mixer.quit.call_count == 2
    ))
    monkeypatch.setattr(player, "_play_with_system_command", fallback)
    player._play_audio_file(AUDIO, threading.Event())
    fallback.assert_called_once()
    assert not player._pygame_initialized
    assert pygame.mixer.quit.call_count == 2
    player._play_audio_file(AUDIO, threading.Event())
    assert pygame.mixer.init.call_count == 3
    pygame.mixer.music.play.assert_called_once()


def test_stuck_busy_times_out_and_next_alarm_plays(player, pygame, monkeypatch):
    clock = SimpleNamespace(now=100.0)

    class Cancellation:
        def is_set(self):
            return False

        def wait(self, seconds):
            clock.now += seconds
            return False

    monkeypatch.setattr(voice, "time", SimpleNamespace(monotonic=lambda: clock.now))
    monkeypatch.setattr(player, "_audio_duration", lambda _: 1)
    pygame.mixer.music.get_busy.return_value = True
    player._play_audio_file(AUDIO, Cancellation())
    assert 110 <= clock.now < 110.2
    assert not player._pygame_initialized
    pygame.mixer.quit.assert_called_once()

    pygame.mixer.music.get_busy.return_value = False
    player._play_audio_file(AUDIO, threading.Event())
    assert pygame.mixer.music.play.call_count == 2
    assert pygame.mixer.init.call_count == 2


def test_healthy_mixer_is_reused(player, pygame):
    player._play_audio_file(AUDIO, threading.Event())
    player._play_audio_file(AUDIO, threading.Event())
    pygame.mixer.init.assert_called_once()
    assert pygame.mixer.music.play.call_count == 2


def test_cancel_during_init_does_not_start_audio(player, pygame):
    cancelled = threading.Event()
    pygame.mixer.init.side_effect = cancelled.set
    player._play_audio_file(AUDIO, cancelled)
    pygame.mixer.music.play.assert_not_called()


def test_cancel_pygame_stops_current_audio(player, pygame):
    cancelled = threading.Event()
    pygame.mixer.music.play.side_effect = cancelled.set
    pygame.mixer.music.get_busy.return_value = True
    player._play_audio_file(AUDIO, cancelled)
    pygame.mixer.music.stop.assert_called_once()


def test_fallback_busy_and_cancel_do_not_poison_next_task(player, monkeypatch):
    entered = [threading.Event(), threading.Event()]
    cancellations = []

    def fallback(path, cancelled, deadline):
        index = len(cancellations)
        cancellations.append(cancelled)
        entered[index].set()
        assert cancelled.wait(2)

    monkeypatch.setattr(player, "_init_pygame", lambda: False)
    monkeypatch.setattr(player, "_play_with_system_command", fallback)
    assert player.initialize()
    player.play_file(AUDIO)
    assert entered[0].wait(1)
    assert player.is_busy  # 队列已空，但后备播放器仍占用任务。
    player.play_file(AUDIO)
    player.stop_playback()
    player.play_file(AUDIO)
    assert entered[1].wait(1)
    assert cancellations[0].is_set()
    assert not cancellations[1].is_set()
    player.stop()
    assert not player._thread.is_alive()
    assert not player.is_busy
    assert player._queue.unfinished_tasks == 0
    assert len(cancellations) == 2


def test_repeated_initialize_keeps_one_worker(player):
    assert player.initialize()
    first = player._thread
    assert player.initialize()
    assert player._thread is first
    player.stop()
    assert not first.is_alive()
    assert player.initialize()
    assert player._thread is not first


def test_worker_survives_task_exception_and_accounts_for_queue(player, monkeypatch):
    completed = threading.Event()
    play = MagicMock(side_effect=[RuntimeError("bad task"), None])

    def run(path, cancelled):
        play(path, cancelled)
        completed.set()

    monkeypatch.setattr(player, "_play_audio_file", run)
    player.initialize()
    player.play_file(AUDIO)
    player.play_file(AUDIO)
    assert completed.wait(1)
    wait_until(lambda: not player.is_busy)
    assert player._thread.is_alive()
    assert player._queue.unfinished_tasks == 0


def test_command_timeout_kills_and_reaps_process(player, monkeypatch):
    processes = []
    real_popen = subprocess.Popen

    def spawn(*args, **kwargs):
        process = real_popen(*args, **kwargs)
        processes.append(process)
        return process

    monkeypatch.setattr(voice.subprocess, "Popen", spawn)
    with pytest.raises(TimeoutError):
        player._run_command(
            [sys.executable, "-c", "import time; time.sleep(30)"],
            threading.Event(), time.monotonic() + 0.15,
        )
    assert processes[0].poll() is not None
    assert processes[0].stderr.closed
    assert player._run_command(
        [sys.executable, "-c", "pass"], threading.Event(), time.monotonic() + 5,
    )


def test_command_cancellation_kills_and_reaps_process(player, monkeypatch):
    cancelled = threading.Event()
    processes = []
    real_popen = subprocess.Popen

    def spawn(*args, **kwargs):
        process = real_popen(*args, **kwargs)
        processes.append(process)
        cancelled.set()
        return process

    monkeypatch.setattr(voice.subprocess, "Popen", spawn)
    assert not player._run_command(
        [sys.executable, "-c", "import time; time.sleep(30)"],
        cancelled, time.monotonic() + 5,
    )
    assert processes[0].poll() is not None
    assert processes[0].stderr.closed


def test_command_failure_preserves_stderr(player):
    with pytest.raises(RuntimeError, match="test device busy"):
        player._run_command(
            [sys.executable, "-c",
             "import sys; sys.stderr.write('test device busy'); sys.exit(2)"],
            threading.Event(), time.monotonic() + 5,
        )


def test_aplay_failure_tries_ffplay(player, monkeypatch):
    monkeypatch.setattr(voice.platform, "system", lambda: "Linux")
    run = MagicMock(side_effect=[RuntimeError("aplay failed"), True])
    monkeypatch.setattr(player, "_run_command", run)
    player._play_with_system_command(AUDIO, threading.Event(), time.monotonic() + 5)
    assert run.call_args_list[0].args[0][0] == "aplay"
    assert run.call_args_list[1].args[0][0] == "ffplay"


@pytest.mark.parametrize("device,expected", [
    ("hw:0,0", ["-D", "plughw:0,0"]),
    ("plughw:1,0", ["-D", "plughw:1,0"]),
    ("custom_pcm", ["-D", "custom_pcm"]),
    (None, []),
])
def test_aplay_uses_service_audio_device(player, monkeypatch, device, expected):
    monkeypatch.setattr(voice.platform, "system", lambda: "Linux")
    if device is None:
        monkeypatch.delenv("AUDIODEV", raising=False)
    else:
        monkeypatch.setenv("AUDIODEV", device)
    run = MagicMock(return_value=True)
    monkeypatch.setattr(player, "_run_command", run)
    player._play_with_system_command(AUDIO, threading.Event(), time.monotonic() + 5)
    assert run.call_args.args[0] == ["aplay", *expected, AUDIO]


def test_zero_volume_never_uses_unscaled_fallback(player, pygame, monkeypatch):
    player.set_volume(0)
    fallback = MagicMock()
    monkeypatch.setattr(player, "_play_with_system_command", fallback)
    player._play_audio_file(AUDIO, threading.Event())
    pygame.mixer.music.play.assert_not_called()
    fallback.assert_not_called()
