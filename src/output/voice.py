"""
语音播报模块
仅支持播放预合成音频文件
"""
import os
import platform
import subprocess
import threading
import time
import queue
import wave
from typing import Optional
from pathlib import Path
from dataclasses import dataclass, field

from ..utils.logger import get_logger


@dataclass
class PlaybackTask:
    """播放任务"""
    file_path: str      # 文件路径
    priority: bool = False
    cancelled: threading.Event = field(default_factory=threading.Event)


class VoicePlayer:
    """语音播报器 - 仅支持音频文件播放"""
    
    _instance: Optional['VoicePlayer'] = None
    
    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance
    
    def __init__(self):
        if hasattr(self, '_initialized'):
            return
        self._initialized = True
        
        self._enabled = True
        self._volume = 1.0
        
        self._queue: queue.Queue[PlaybackTask] = queue.Queue()
        self._running = False
        self._thread: Optional[threading.Thread] = None
        self._logger = get_logger()
        self._state_lock = threading.RLock()
        self._wake = threading.Event()
        self._active_task: Optional[PlaybackTask] = None
        
        # pygame用于音频文件播放
        self._pygame_initialized = False
    
    def initialize(self, enabled: bool = True, volume: float = 1.0) -> bool:
        """
        初始化语音播报器
        """
        with self._state_lock:
            self._enabled = enabled
            self._volume = max(0.0, min(1.0, volume))
            if not enabled:
                self.stop_playback()
                self._logger.info("语音播报已禁用")
                return True
            if self._thread and self._thread.is_alive():
                # 退出中的线程仍可能持有声卡，不能另开一个消费者。
                return self._running
            self._running = True
            self._thread = threading.Thread(
                target=self._play_loop, daemon=True, name="voice-player"
            )
            self._thread.start()
            return True
    
    def _init_pygame(self) -> bool:
        """初始化pygame mixer用于音频播放"""
        try:
            import pygame
            if self._pygame_initialized and pygame.mixer.get_init():
                return True
            pygame.mixer.init()
            self._pygame_initialized = True
            self._logger.info("pygame mixer初始化成功")
            return True
        except Exception as e:
            self._logger.warning(f"pygame初始化失败，将使用系统命令播放: {e}")
            self._reset_pygame()
            return False

    def _reset_pygame(self):
        """仅由播放线程释放声卡，避免与播放/音量操作并发。"""
        self._pygame_initialized = False
        try:
            import pygame
            pygame.mixer.quit()
        except Exception as e:
            self._logger.debug(f"释放音频设备: {e}")

    def _play_loop(self):
        """播放循环"""
        try:
            while self._running:
                # 出队和标记播放必须原子完成，否则 stop_playback 会漏掉刚出队的任务。
                with self._state_lock:
                    if not self._running:
                        break
                    try:
                        task = self._queue.get_nowait()
                    except queue.Empty:
                        task = None
                        self._wake.clear()
                    self._active_task = task
                if task is None:
                    self._wake.wait(1.0)
                    continue
                try:
                    self._play_audio_file(task.file_path, task.cancelled)
                except Exception as e:
                    self._logger.error(f"播放语音失败: {e}")
                    self._reset_pygame()
                finally:
                    with self._state_lock:
                        self._active_task = None
                        self._queue.task_done()
        finally:
            self._reset_pygame()

    @staticmethod
    def _audio_duration(file_path: str) -> float:
        try:
            with wave.open(file_path, "rb") as audio:
                return audio.getnframes() / audio.getframerate()
        except (OSError, EOFError, wave.Error, ZeroDivisionError):
            return 25.0

    def _play_audio_file(self, file_path: str, cancelled: threading.Event):
        """播放音频文件"""
        if not os.path.exists(file_path):
            self._logger.warning(f"音频文件不存在: {file_path}")
            return

        # 同一任务的重试及后备播放共用一个期限，异常时不能无限占用消费者。
        timeout = max(10.0, min(120.0, self._audio_duration(file_path) + 5.0))
        deadline = time.monotonic() + timeout
        for attempt in range(2):
            if cancelled.is_set() or self._volume <= 0:
                return
            if time.monotonic() >= deadline:
                return
            if not self._init_pygame():
                break
            if cancelled.is_set():
                return
            try:
                import pygame
                pygame.mixer.music.load(file_path)
                pygame.mixer.music.set_volume(self._volume)
                if cancelled.is_set():
                    return
                pygame.mixer.music.play()
                volume = self._volume
                while pygame.mixer.music.get_busy():
                    if cancelled.is_set():
                        pygame.mixer.music.stop()
                        return
                    if time.monotonic() >= deadline:
                        raise TimeoutError(f"播放超过 {timeout:.1f}s: {file_path}")
                    if volume != self._volume:
                        volume = self._volume
                        pygame.mixer.music.set_volume(volume)
                    cancelled.wait(0.1)
                self._logger.debug(f"播放音频文件完成: {file_path}")
                return
            except Exception as e:
                self._logger.error(f"播放音频文件失败（第 {attempt + 1} 次）: {e}")
                # 先释放原 mixer，再重建或使用后备命令，避免一直复用失效句柄。
                self._reset_pygame()
        if not cancelled.is_set() and time.monotonic() < deadline:
            self._play_with_system_command(file_path, cancelled, deadline)

    def _run_command(self, command: list, cancelled: threading.Event, deadline: float) -> bool:
        """可取消、有期限的子进程播放；退出前回收子进程。"""
        process = subprocess.Popen(command, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
        try:
            while not cancelled.is_set():
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise TimeoutError(f"系统命令播放超时: {command[0]}")
                try:
                    _, stderr = process.communicate(timeout=min(0.1, remaining))
                except subprocess.TimeoutExpired:
                    continue
                if process.returncode:
                    detail = (stderr or b"").decode("utf-8", errors="replace")[-500:]
                    raise RuntimeError(f"{command[0]} rc={process.returncode}: {detail}")
                return True
            return False
        finally:
            if process.poll() is None:
                process.kill()
                try:
                    process.communicate(timeout=1.0)
                except subprocess.TimeoutExpired:
                    self._logger.error(f"播放子进程未及时退出: pid={process.pid}")
            if process.stderr:
                process.stderr.close()

    def _play_with_system_command(self, file_path: str, cancelled: threading.Event,
                                  deadline: float):
        """使用系统命令播放音频（后备方案）。"""
        try:
            system = platform.system()
            if system == "Linux":
                # 无桌面会话时 ALSA default 可能指向不可用的 Pulse/PipeWire。
                # 与服务的 SDL 使用同一声卡；plughw 兼容 WAV 的采样率/声道。
                device = os.environ.get("AUDIODEV")
                if device and device.startswith("hw:"):
                    device = "plughw:" + device[3:]
                aplay = ["aplay"]
                if device:
                    aplay.extend(["-D", device])
                aplay.append(file_path)
                for command in (
                    aplay,
                    ['ffplay', '-nodisp', '-autoexit', '-volume',
                     str(round(self._volume * 100)), file_path],
                ):
                    if cancelled.is_set() or self._volume <= 0 or time.monotonic() >= deadline:
                        return
                    try:
                        if self._run_command(command, cancelled, deadline):
                            self._logger.debug(f"系统命令播放完成: {file_path}")
                        return
                    except (OSError, RuntimeError, TimeoutError) as e:
                        self._logger.warning(f"后备播放失败: {e}")
            elif system == "Windows":
                import winsound
                if cancelled.is_set() or self._volume <= 0:
                    return
                try:
                    winsound.PlaySound(file_path, winsound.SND_FILENAME | winsound.SND_ASYNC)
                    cancelled.wait(max(0, min(
                        self._audio_duration(file_path), deadline - time.monotonic()
                    )))
                finally:
                    winsound.PlaySound(None, 0)
        except Exception as e:
            self._logger.error(f"系统命令播放失败: {e}")
            
    def play_file(self, file_path: str, priority: bool = False):
        """
        播放音频文件
        
        Args:
            file_path: 音频文件路径
            priority: 是否优先播放（清空队列）
        """
        with self._state_lock:
            if not self._enabled or not self._running:
                self._logger.debug(f"语音未启用或已停止，跳过文件: {file_path}")
                return
            if priority:
                self._clear_queue()
            task = PlaybackTask(file_path=file_path, priority=priority)
            self._queue.put(task)
            self._wake.set()
        self._logger.info(f"音频文件已加入队列: {file_path}")
    
    def play_zone_audio(self, zone_id: str, audio_type: str, priority: bool = False):
        """
        播放灶台预合成音频
        
        Args:
            zone_id: 灶台ID
            audio_type: 音频类型 (warning/alarm/action/temp_alarm)
            priority: 是否优先播放(清空队列)
        """
        # 音频资源目录
        AUDIO_ASSETS_DIR = str(Path(__file__).resolve().parents[2] / "audio_assets")
        
        # 检查监测模式
        try:
            from ..utils.config import config_manager
            zone_mode = config_manager.config.system.zone_mode
        except Exception:
            zone_mode = "zoned"  # 默认分区模式
        
        # 根据模式选择音频目录
        if zone_mode == "single":
            audio_path = os.path.join(AUDIO_ASSETS_DIR, "no_zone", f"{audio_type}.wav")
        else:
            audio_path = os.path.join(AUDIO_ASSETS_DIR, zone_id, f"{audio_type}.wav")
        
        # 播放音频
        if os.path.exists(audio_path):
            self.play_file(audio_path, priority=priority)
        else:
            self._logger.warning(f"未找到音频文件: zone={zone_id}, type={audio_type}, path={audio_path}")
    
    def speak_warning(self, zone_id: str, zone_name: str):
        """播放预警语音"""
        self.play_zone_audio(zone_id, "warning", priority=False)
    
    def speak_alarm(self, zone_id: str, zone_name: str):
        """播放报警语音"""
        self.play_zone_audio(zone_id, "alarm", priority=False)
    
    def speak_cutoff(self, zone_id: str, zone_name: str):
        """播放切电语音"""
        self.play_zone_audio(zone_id, "action", priority=False)
    
    def speak_temp_alarm(self, zone_id: str, zone_name: str):
        """播放温度报警语音"""
        self.play_zone_audio(zone_id, "temp_alarm", priority=False)
    
    def _clear_queue(self):
        """清空队列"""
        with self._state_lock:
            while True:
                try:
                    self._queue.get_nowait()
                    self._queue.task_done()
                except queue.Empty:
                    break
    
    def stop_playback(self):
        """
        停止当前播放并清空队列
        
        用于灶台停用或进入巡检模式时立即停止所有播报
        """
        with self._state_lock:
            self._clear_queue()
            if self._active_task:
                self._active_task.cancelled.set()
            self._wake.set()
    
    def stop(self):
        """停止语音播报器"""
        with self._state_lock:
            self._running = False
            self.stop_playback()
        if self._thread:
            self._thread.join(timeout=3.0)
            if self._thread.is_alive():
                self._logger.error("语音播放线程未及时退出")
                return
        self._logger.info("语音播报器已停止")
    
    def set_volume(self, volume: float):
        """动态设置音量

        Args:
            volume: 音量值 (0.0 - 1.0)
        """
        self._volume = max(0.0, min(1.0, volume))
        self._logger.info(f"音量已设置为: {self._volume * 100:.0f}%")

        # 播放线程在下一次轮询中应用，避免不同线程并发操作 mixer。
    
    @property
    def volume(self) -> float:
        """获取当前音量"""
        return self._volume
    
    @property
    def is_enabled(self) -> bool:
        return self._enabled

    @property
    def is_busy(self) -> bool:
        """是否正在播放或队列中有任务"""
        with self._state_lock:
            return self._active_task is not None or not self._queue.empty()

# 全局语音播报器实例
voice_player = VoicePlayer()
