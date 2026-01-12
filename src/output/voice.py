"""
语音播报模块
仅支持播放预合成音频文件
"""
import os
import threading
import queue
from typing import Optional
from pathlib import Path
from dataclasses import dataclass

from ..utils.logger import get_logger


@dataclass
class PlaybackTask:
    """播放任务"""
    file_path: str      # 文件路径
    priority: bool = False


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
        
        # pygame用于音频文件播放
        self._pygame_initialized = False
    
    def initialize(self, enabled: bool = True, volume: float = 1.0) -> bool:
        """
        初始化语音播报器
        """
        self._enabled = enabled
        self._volume = volume
        
        if not enabled:
            self._logger.info("语音播报已禁用")
            return True
        
        # 启动播放线程
        self._running = True
        self._thread = threading.Thread(target=self._play_loop, daemon=True)
        self._thread.start()
        
        return True
    
    def _init_pygame(self) -> bool:
        """初始化pygame mixer用于音频播放"""
        if self._pygame_initialized:
            return True
        
        try:
            import pygame
            pygame.mixer.init()
            self._pygame_initialized = True
            self._logger.info("pygame mixer初始化成功")
            return True
        except Exception as e:
            self._logger.warning(f"pygame初始化失败，将使用系统命令播放: {e}")
            return False
            
    def _play_loop(self):
        """播放循环"""
        self._init_pygame()
        
        while self._running:
            try:
                # 从队列获取任务，超时1秒
                task = self._queue.get(timeout=1.0)
                if task is None:
                    continue
                
                self._play_audio_file(task.file_path)
                    
                self._queue.task_done()
                
            except queue.Empty:
                continue
            except Exception as e:
                self._logger.error(f"播放语音失败: {e}")
    
    def _play_audio_file(self, file_path: str):
        """播放音频文件"""
        if not os.path.exists(file_path):
            self._logger.warning(f"音频文件不存在: {file_path}")
            return
        
        try:
            if self._pygame_initialized:
                import pygame
                pygame.mixer.music.load(file_path)
                pygame.mixer.music.set_volume(self._volume)
                pygame.mixer.music.play()
                
                # 等待播放完成
                while pygame.mixer.music.get_busy():
                    pygame.time.Clock().tick(10)
                
                self._logger.debug(f"播放音频文件完成: {file_path}")
            else:
                # 后备方案：使用系统命令
                self._play_with_system_command(file_path)
                
        except Exception as e:
            self._logger.error(f"播放音频文件失败: {e}")
            # 尝试使用系统命令作为后备
            self._play_with_system_command(file_path)
    
    def _play_with_system_command(self, file_path: str):
        """使用系统命令播放音频（后备方案）"""
        import platform
        import subprocess
        
        try:
            system = platform.system()
            if system == "Linux":
                # 尝试使用aplay或ffplay
                try:
                    subprocess.run(
                        ['aplay', file_path],
                        check=True,
                        capture_output=True
                    )
                except FileNotFoundError:
                    subprocess.run(
                        ['ffplay', '-nodisp', '-autoexit', file_path],
                        check=True,
                        capture_output=True
                    )
            elif system == "Windows":
                import winsound
                winsound.PlaySound(file_path, winsound.SND_FILENAME)
            
            self._logger.debug(f"系统命令播放完成: {file_path}")
        except Exception as e:
            self._logger.error(f"系统命令播放失败: {e}")
            
    def play_file(self, file_path: str, priority: bool = False):
        """
        播放音频文件
        
        Args:
            file_path: 音频文件路径
            priority: 是否优先播放（清空队列）
        """
        if not self._enabled:
            self._logger.debug(f"语音禁用，跳过文件: {file_path}")
            return
        
        if priority:
            self._clear_queue()
        
        task = PlaybackTask(file_path=file_path, priority=priority)
        self._queue.put(task)
        self._logger.info(f"音频文件已加入队列: {file_path}")
    
    def play_zone_audio(self, zone_id: str, audio_type: str, priority: bool = False):
        """
        播放灶台预合成音频
        
        Args:
            zone_id: 灶台ID
            audio_type: 音频类型 (warning/alarm/action)
            priority: 是否优先播放(清空队列)
        """
        # 尝试从TTS管理器获取预合成音频路径
        try:
            from ..tts.tts_manager import tts_manager, AudioType
            
            audio_type_enum = AudioType[audio_type.upper()]
            audio_path = tts_manager.get_audio_path(zone_id, audio_type_enum)
            
            if audio_path:
                self.play_file(audio_path, priority=priority)
            else:
                 self._logger.warning(f"未找到音频文件: zone={zone_id}, type={audio_type}")
        except Exception as e:
            self._logger.debug(f"获取预合成音频失败: {e}")
    
    def speak_warning(self, zone_id: str, zone_name: str):
        """播放预警语音"""
        self.play_zone_audio(zone_id, "warning", priority=False)
    
    def speak_alarm(self, zone_id: str, zone_name: str):
        """播放报警语音"""
        self.play_zone_audio(zone_id, "alarm", priority=False)
    
    def speak_cutoff(self, zone_id: str, zone_name: str):
        """播放切电语音"""
        self.play_zone_audio(zone_id, "action", priority=False)
    
    def _clear_queue(self):
        """清空队列"""
        while not self._queue.empty():
            try:
                self._queue.get_nowait()
            except queue.Empty:
                break
    
    def stop(self):
        """停止语音播报器"""
        self._running = False
        if self._thread:
            self._thread.join(timeout=2.0)
        
        self._clear_queue()
        
        if self._pygame_initialized:
            try:
                import pygame
                pygame.mixer.quit()
            except:
                pass
            self._pygame_initialized = False
        
        self._logger.info("语音播报器已停止")
    
    def set_volume(self, volume: float):
        """动态设置音量
        
        Args:
            volume: 音量值 (0.0 - 1.0)
        """
        self._volume = max(0.0, min(1.0, volume))
        self._logger.info(f"音量已设置为: {self._volume * 100:.0f}%")
        
        # 如果正在播放，立即更新音量
        if self._pygame_initialized:
            try:
                import pygame
                if pygame.mixer.get_init():
                    pygame.mixer.music.set_volume(self._volume)
            except Exception as e:
                self._logger.warning(f"更新音量失败: {e}")
    
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
        # 检查队列是否有任务
        if not self._queue.empty():
            return True
        
        # 检查是否正在播放(pygame)
        if self._pygame_initialized:
            try:
                import pygame
                if pygame.mixer.get_init() and pygame.mixer.music.get_busy():
                    return True
            except:
                pass
                
        return False

# 全局语音播报器实例
voice_player = VoicePlayer()
