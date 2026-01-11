"""
TTS管理器模块
智能生命周期管理的Kokoro TTS服务
支持延迟销毁和后台合成任务队列
"""
import os
import threading
import queue
import time
from typing import Optional, Dict, List, Callable
from pathlib import Path
from dataclasses import dataclass
from enum import Enum

from ..utils.logger import get_logger


class AudioType(Enum):
    """音频类型"""
    WARNING = "warning"     # 预警
    ALARM = "alarm"         # 报警
    ACTION = "action"       # 切电
    # 巡检语音类型
    PATROL_HAS_PERSON = "patrol_has_person"    # {zone_name}有人
    PATROL_NO_PERSON = "patrol_no_person"      # {zone_name}离人
    PATROL_PERSON_OK = "patrol_person_ok"      # {zone_name}离人监测正常
    PATROL_FIRE_ON = "patrol_fire_on"          # {zone_name}动火中
    PATROL_FIRE_OFF = "patrol_fire_off"        # {zone_name}已关火
    PATROL_FIRE_OK = "patrol_fire_ok"          # {zone_name}动火监测正常


@dataclass
class SynthesisTask:
    """合成任务"""
    zone_id: str
    zone_name: str
    audio_type: AudioType
    text: str
    callback: Optional[Callable[[bool, str], None]] = None


class KokoroService:
    """Kokoro TTS服务封装"""
    
    def __init__(self, model_path: str, voice_path: str, config_path: str):
        self._model_path = model_path
        self._voice_path = voice_path
        self._config_path = config_path
        self._kokoro = None
        self._g2p = None
        self._loaded = False
        self._logger = get_logger()
    
    def load(self) -> bool:
        """加载Kokoro引擎"""
        if self._loaded:
            return True
        
        try:
            self._logger.info("正在加载Kokoro TTS引擎...")
            import warnings
            warnings.filterwarnings("ignore", category=SyntaxWarning)
            
            from misaki import zh
            from kokoro_onnx import Kokoro
            
            self._g2p = zh.ZHG2P(version="1.1")
            self._kokoro = Kokoro(
                self._model_path, 
                self._voice_path, 
                vocab_config=self._config_path
            )
            
            self._loaded = True
            self._logger.info("Kokoro TTS引擎加载成功")
            return True
            
        except Exception as e:
            self._logger.error(f"Kokoro TTS引擎加载失败: {e}")
            return False
    
    def unload(self):
        """卸载Kokoro引擎，释放内存"""
        if not self._loaded:
            return
        
        try:
            self._logger.info("正在卸载Kokoro TTS引擎...")
            self._kokoro = None
            self._g2p = None
            self._loaded = False
            
            # 强制垃圾回收
            import gc
            gc.collect()
            
            self._logger.info("Kokoro TTS引擎已卸载")
            
        except Exception as e:
            self._logger.error(f"卸载Kokoro引擎失败: {e}")
    
    def synthesize(self, text: str, output_path: str, speed: float = 1.0) -> bool:
        """合成语音"""
        if not self._loaded:
            if not self.load():
                return False
        
        try:
            import soundfile as sf
            
            phonemes, _ = self._g2p(text)
            samples, sample_rate = self._kokoro.create(
                phonemes, voice="zf_001", speed=speed, is_phonemes=True
            )
            
            # 确保目录存在
            os.makedirs(os.path.dirname(output_path), exist_ok=True)
            
            sf.write(output_path, samples, sample_rate)
            return True
            
        except Exception as e:
            self._logger.error(f"语音合成失败: {e}")
            return False
    
    @property
    def is_loaded(self) -> bool:
        return self._loaded


class TTSManager:
    """
    TTS管理器 - 智能生命周期管理
    
    特性：
    - 延迟销毁：空闲一定时间后自动卸载引擎
    - 任务队列：支持后台批量合成
    - 线程安全：支持多线程任务提交
    """
    
    _instance: Optional['TTSManager'] = None
    
    # 配置常量
    IDLE_TIMEOUT = 150  # 空闲150秒后销毁服务
    
    # 资源路径（相对于项目根目录）
    MODEL_FILE = "./src/tts/assets/kokoro-v1.1-zh.onnx"
    VOICE_FILE = "./src/tts/assets/voices-v1.1-zh.bin"
    CONFIG_FILE = "./src/tts/config/config.json"
    AUDIO_DIR = "audio_assets"
    
    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance
    
    def __init__(self):
        if hasattr(self, '_initialized'):
            return
        self._initialized = True
        
        self._logger = get_logger()
        self._service: Optional[KokoroService] = None
        
        # 任务队列
        self._task_queue: queue.Queue[SynthesisTask] = queue.Queue()
        self._worker_thread: Optional[threading.Thread] = None
        self._running = False
        
        # 生命周期管理
        self._last_activity_time = 0.0
        self._lifecycle_thread: Optional[threading.Thread] = None
        self._lock = threading.Lock()
        
        # 合成状态跟踪 {zone_id: status}
        # status: "none" | "queued" | "synthesizing" | "completed" | "failed"
        self._synthesis_status: Dict[str, str] = {}
        self._synthesis_progress: Dict[str, int] = {}  # 合成进度 0-3
        
        # 消息模板（将从配置加载）
        self._warning_message = "动火区域离人即将超时，请立即回到工作岗位"
        self._alarm_message = "动火区域离人超时，请立即回到工作岗位"
        self._action_message = "动火区域离人超时，已自动切断炉灶电源，请立即现场处理"
    
    def initialize(self, 
                   audio_dir: str = None,
                   idle_timeout: int = None,
                   warning_message: str = None,
                   alarm_message: str = None,
                   action_message: str = None) -> bool:
        """
        初始化TTS管理器
        
        Args:
            audio_dir: 音频输出目录
            idle_timeout: 空闲超时时间（秒）
            warning_message: 预警消息模板
            alarm_message: 报警消息模板
            action_message: 切电消息模板
        """
        if audio_dir:
            self.AUDIO_DIR = audio_dir
        if idle_timeout:
            self.IDLE_TIMEOUT = idle_timeout
        if warning_message:
            self._warning_message = warning_message
        if alarm_message:
            self._alarm_message = alarm_message
        if action_message:
            self._action_message = action_message
        
        # 确保音频目录存在
        os.makedirs(self.AUDIO_DIR, exist_ok=True)
        
        # 启动工作线程
        self._running = True
        self._worker_thread = threading.Thread(target=self._worker_loop, daemon=True)
        self._worker_thread.start()
        
        # 启动生命周期管理线程
        self._lifecycle_thread = threading.Thread(target=self._lifecycle_loop, daemon=True)
        self._lifecycle_thread.start()
        
        self._logger.info("TTS管理器初始化完成")
        return True
    
    def update_messages(self, 
                        warning_message: str = None,
                        alarm_message: str = None,
                        action_message: str = None):
        """更新消息模板"""
        if warning_message:
            self._warning_message = warning_message
        if alarm_message:
            self._alarm_message = alarm_message
        if action_message:
            self._action_message = action_message
    
    def submit_synthesis_task(self, zone_id: str, zone_name: str, 
                               callback: Callable[[bool, str], None] = None):
        """
        提交灶台语音合成任务
        
        为指定灶台合成预警、报警、切电以及巡检相关语音
        
        Args:
            zone_id: 灶台ID
            zone_name: 灶台名称
            callback: 完成回调 (success, message)
        """
        self._logger.info(f"提交语音合成任务: {zone_id} ({zone_name})")
        
        # 设置状态为队列中
        with self._lock:
            self._synthesis_status[zone_id] = "queued"
            self._synthesis_progress[zone_id] = 0
        
        # 创建所有语音的合成任务（预警、报警、切电 + 巡检语音）
        tasks = [
            # 预警/报警/切电语音
            SynthesisTask(
                zone_id=zone_id,
                zone_name=zone_name,
                audio_type=AudioType.WARNING,
                text=f"警告，{zone_name}{self._warning_message}",
                callback=None
            ),
            SynthesisTask(
                zone_id=zone_id,
                zone_name=zone_name,
                audio_type=AudioType.ALARM,
                text=f"警告，{zone_name}{self._alarm_message}",
                callback=None
            ),
            SynthesisTask(
                zone_id=zone_id,
                zone_name=zone_name,
                audio_type=AudioType.ACTION,
                text=f"警告，警告，{zone_name}{self._action_message}",
                callback=None
            ),
            # 巡检语音
            SynthesisTask(
                zone_id=zone_id,
                zone_name=zone_name,
                audio_type=AudioType.PATROL_HAS_PERSON,
                text=f"{zone_name}有人",
                callback=None
            ),
            SynthesisTask(
                zone_id=zone_id,
                zone_name=zone_name,
                audio_type=AudioType.PATROL_NO_PERSON,
                text=f"{zone_name}离人",
                callback=None
            ),
            SynthesisTask(
                zone_id=zone_id,
                zone_name=zone_name,
                audio_type=AudioType.PATROL_PERSON_OK,
                text=f"{zone_name}离人监测正常",
                callback=None
            ),
            SynthesisTask(
                zone_id=zone_id,
                zone_name=zone_name,
                audio_type=AudioType.PATROL_FIRE_ON,
                text=f"{zone_name}动火中",
                callback=None
            ),
            SynthesisTask(
                zone_id=zone_id,
                zone_name=zone_name,
                audio_type=AudioType.PATROL_FIRE_OFF,
                text=f"{zone_name}已关火",
                callback=None
            ),
            SynthesisTask(
                zone_id=zone_id,
                zone_name=zone_name,
                audio_type=AudioType.PATROL_FIRE_OK,
                text=f"{zone_name}动火监测正常",
                callback=callback  # 最后一个任务完成时回调
            ),
        ]
        
        for task in tasks:
            self._task_queue.put(task)
    
    def get_audio_path(self, zone_id: str, audio_type: AudioType) -> Optional[str]:
        """
        获取预合成音频文件路径
        
        Args:
            zone_id: 灶台ID
            audio_type: 音频类型
            
        Returns:
            音频文件路径，如不存在则返回None
        """
        path = os.path.join(self.AUDIO_DIR, zone_id, f"{audio_type.value}.wav")
        if os.path.exists(path):
            return path
        return None
    
    def has_audio_files(self, zone_id: str) -> bool:
        """检查灶台是否有完整的音频文件"""
        for audio_type in AudioType:
            if self.get_audio_path(zone_id, audio_type) is None:
                return False
        return True
    
    def delete_audio_files(self, zone_id: str):
        """删除灶台的音频文件"""
        zone_dir = os.path.join(self.AUDIO_DIR, zone_id)
        if os.path.exists(zone_dir):
            import shutil
            shutil.rmtree(zone_dir)
            self._logger.info(f"已删除灶台 {zone_id} 的音频文件")
    
    def _worker_loop(self):
        """工作线程循环"""
        self._logger.info("TTS工作线程已启动")
        
        while self._running:
            try:
                # 从队列获取任务，超时1秒
                task = self._task_queue.get(timeout=1.0)
                
                if task is None:
                    continue
                
                self._process_task(task)
                self._task_queue.task_done()
                
            except queue.Empty:
                continue
            except Exception as e:
                self._logger.error(f"TTS工作线程错误: {e}")
        
        self._logger.info("TTS工作线程已停止")
    
    def _process_task(self, task: SynthesisTask):
        """处理合成任务"""
        # 更新状态为合成中
        with self._lock:
            self._synthesis_status[task.zone_id] = "synthesizing"
            self._last_activity_time = time.time()
            
            # 确保服务已加载
            if self._service is None:
                self._service = KokoroService(
                    self.MODEL_FILE,
                    self.VOICE_FILE,
                    self.CONFIG_FILE
                )
            
            if not self._service.is_loaded:
                if not self._service.load():
                    self._synthesis_status[task.zone_id] = "failed"
                    if task.callback:
                        task.callback(False, "引擎加载失败")
                    return
        
        # 执行合成（不需要锁，因为Kokoro是线程安全的）
        output_path = os.path.join(
            self.AUDIO_DIR, 
            task.zone_id, 
            f"{task.audio_type.value}.wav"
        )
        
        self._logger.info(f"正在合成: {task.zone_name} - {task.audio_type.value}")
        
        success = self._service.synthesize(task.text, output_path)
        
        with self._lock:
            self._last_activity_time = time.time()
            # 更新进度
            self._synthesis_progress[task.zone_id] = self._synthesis_progress.get(task.zone_id, 0) + 1
            
            # 如果是最后一个任务（PATROL_FIRE_OK），更新状态
            if task.audio_type == AudioType.PATROL_FIRE_OK:
                if success and self.has_audio_files(task.zone_id):
                    self._synthesis_status[task.zone_id] = "completed"
                elif not success:
                    self._synthesis_status[task.zone_id] = "failed"
        
        if success:
            self._logger.info(f"合成成功: {output_path}")
        else:
            self._logger.error(f"合成失败: {task.zone_name} - {task.audio_type.value}")
        
        if task.callback:
            task.callback(success, output_path if success else "合成失败")
    
    def _lifecycle_loop(self):
        """生命周期管理线程"""
        self._logger.info("TTS生命周期管理线程已启动")
        
        while self._running:
            try:
                time.sleep(10)  # 每10秒检查一次
                
                with self._lock:
                    if self._service is None or not self._service.is_loaded:
                        continue
                    
                    # 检查是否超过空闲时间
                    idle_time = time.time() - self._last_activity_time
                    if idle_time >= self.IDLE_TIMEOUT:
                        self._logger.info(f"TTS服务空闲 {idle_time:.0f} 秒，正在卸载...")
                        self._service.unload()
                
            except Exception as e:
                self._logger.error(f"生命周期管理错误: {e}")
        
        self._logger.info("TTS生命周期管理线程已停止")
    
    def stop(self):
        """停止TTS管理器"""
        self._running = False
        
        # 等待工作线程完成
        if self._worker_thread:
            self._worker_thread.join(timeout=2.0)
        
        if self._lifecycle_thread:
            self._lifecycle_thread.join(timeout=2.0)
        
        # 卸载服务
        with self._lock:
            if self._service:
                self._service.unload()
                self._service = None
        
        self._logger.info("TTS管理器已停止")
    
    def get_pending_count(self) -> int:
        """获取待处理任务数"""
        return self._task_queue.qsize()
    
    def is_service_loaded(self) -> bool:
        """检查服务是否已加载"""
        with self._lock:
            return self._service is not None and self._service.is_loaded
    
    def get_synthesis_status(self, zone_id: str) -> str:
        """
        获取指定灶台的语音合成状态
        
        Returns:
            状态字符串: "none" | "queued" | "synthesizing" | "completed" | "failed"
        """
        with self._lock:
            # 如果有完整的音频文件，返回已完成
            if self.has_audio_files(zone_id):
                return "completed"
            
            # 返回跟踪的状态
            return self._synthesis_status.get(zone_id, "none")
    
    def get_all_synthesis_status(self) -> Dict[str, str]:
        """获取所有灶台的语音合成状态"""
        with self._lock:
            result = {}
            for zone_id in self._synthesis_status:
                if self.has_audio_files(zone_id):
                    result[zone_id] = "completed"
                else:
                    result[zone_id] = self._synthesis_status[zone_id]
            return result


# 全局TTS管理器实例
tts_manager = TTSManager()
