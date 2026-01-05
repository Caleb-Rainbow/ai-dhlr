"""
语音播报模块
支持TTS语音合成和预录制音频播放
"""
import threading
import queue
from typing import Optional
from pathlib import Path

from ..utils.logger import get_logger


class VoicePlayer:
    """语音播报器"""
    
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
        self._engine_type = "pyttsx3"
        self._rate = 150
        self._volume = 1.0
        
        self._tts_engine = None
        self._queue = queue.Queue()
        self._running = False
        self._thread: Optional[threading.Thread] = None
        self._logger = get_logger()
    
    def initialize(self, enabled: bool = True, engine: str = "pyttsx3",
                   rate: int = 150, volume: float = 1.0) -> bool:
        """
        初始化语音播报器
        """
        self._enabled = enabled
        self._engine_type = engine
        self._rate = rate
        self._volume = volume
        
        if not enabled:
            self._logger.info("语音播报已禁用")
            return True
        
        # 启动播放线程（引擎初始化将在线程中进行）
        self._running = True
        self._thread = threading.Thread(target=self._play_loop, daemon=True)
        self._thread.start()
        
        return True
    
    def _init_engine(self):
        """在线程内部初始化引擎"""
        try:
            if self._engine_type == "pyttsx3":
                import pyttsx3
                self._tts_engine = pyttsx3.init()
                self._tts_engine.setProperty('rate', self._rate)
                self._tts_engine.setProperty('volume', self._volume)
                
                # 尝试设置中文语音
                voices = self._tts_engine.getProperty('voices')
                for voice in voices:
                    if 'chinese' in voice.name.lower() or 'zh' in voice.id.lower():
                        self._tts_engine.setProperty('voice', voice.id)
                        break
                
                self._logger.info("pyttsx3 语音引擎初始化成功")
        except Exception as e:
            self._logger.error(f"语音引擎初始化失败: {e}")
            self._enabled = False

    def _play_loop(self):
        """播放循环"""
        self._init_engine()
        
        while self._running:
            try:
                # 从队列获取消息，超时1秒
                message = self._queue.get(timeout=1.0)
                if message is None:
                    continue
                
                self._play_message(message)
                self._queue.task_done()
                
            except queue.Empty:
                continue
            except Exception as e:
                self._logger.error(f"播放语音失败: {e}")
    
    def _play_message(self, message: str):
        """播放单条消息"""
        if not self._enabled:
            return
        
        try:
            if self._engine_type == "pyttsx3" and self._tts_engine:
                self._tts_engine.say(message)
                self._tts_engine.runAndWait()
                self._logger.debug(f"播放语音: {message}")
            
        except Exception as e:
            self._logger.error(f"播放语音失败: {e}")
    
    def speak(self, message: str, priority: bool = False):
        """
        播放语音
        
        Args:
            message: 要播放的文本
            priority: 是否优先播放（清空队列）
        """
        if not self._enabled:
            self._logger.debug(f"语音禁用，跳过: {message}")
            return
        
        if priority:
            # 清空队列
            while not self._queue.empty():
                try:
                    self._queue.get_nowait()
                except queue.Empty:
                    break
        
        self._queue.put(message)
        self._logger.info(f"语音已加入队列: {message}")
    
    def speak_warning(self, zone_name: str):
        """播放预警语音"""
        message = f"警告，{zone_name}无人看管，请注意安全。"
        self.speak(message, priority=True)
    
    def speak_cutoff(self, zone_name: str):
        """播放切电语音"""
        message = f"警告，{zone_name}无人看管超时，已自动切断电源。"
        self.speak(message, priority=True)
    
    def stop(self):
        """停止语音播报器"""
        self._running = False
        if self._thread:
            self._thread.join(timeout=2.0)
        
        # 清空队列
        while not self._queue.empty():
            try:
                self._queue.get_nowait()
            except queue.Empty:
                break
        
        if self._tts_engine:
            try:
                self._tts_engine.stop()
            except:
                pass
            self._tts_engine = None
        
        self._logger.info("语音播报器已停止")
    
    @property
    def is_enabled(self) -> bool:
        return self._enabled
    
    def test_speak(self, text: str = None) -> bool:
        """
        测试语音播报
        
        Args:
            text: 测试文本，默认为系统测试语音
        
        Returns:
            是否成功加入队列
        """
        if not self._enabled:
            self._logger.warning("语音播报已禁用，无法测试")
            return False
        
        test_text = text or "语音播报测试成功，系统运行正常。"
        self.speak(test_text, priority=True)
        return True


# 全局语音播报器实例
voice_player = VoicePlayer()
