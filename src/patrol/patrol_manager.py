"""
巡检管理器模块
实现设备巡检、自检、演示和强制动作功能
"""
import time
import threading
import asyncio
import os
from typing import Optional, Dict, List, Callable
from dataclasses import dataclass, field
from enum import Enum

from ..utils.logger import get_logger
from ..output.voice import voice_player
from ..api.websocket import sync_upload_alarm_record


# 音频资源目录
AUDIO_ASSETS_DIR = "audio_assets"


def _get_audio_path(zone_id: str, audio_type: str) -> Optional[str]:
    """获取灶台音频文件路径
    
    在不分区模式下(zone_mode='single')，使用 no_zone 目录的音频
    """
    # 检查监测模式
    try:
        from .utils.config import config_manager
        zone_mode = config_manager.config.system.zone_mode
    except Exception:
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
    
    if os.path.exists(audio_path):
        return audio_path
    return None



class PatrolStep(Enum):
    """巡检步骤"""
    IDLE = "idle"                          # 空闲
    SELF_CHECK_PERSON = "self_check_person"  # 自检-离人检测
    SELF_CHECK_FIRE = "self_check_fire"      # 自检-动火检测
    ALARM_DEMO = "alarm_demo"                # 报警演示
    FORCE_WARNING = "force_warning"          # 强制预警
    FORCE_ALARM = "force_alarm"              # 强制报警
    FORCE_CUTOFF = "force_cutoff"            # 强制切电


@dataclass
class PatrolResult:
    """巡检结果"""
    zone_id: str
    zone_name: str
    step: str
    status: str  # "success", "warning", "error"
    message: str
    timestamp: float = field(default_factory=time.time)
    
    def to_dict(self) -> dict:
        return {
            "zone_id": self.zone_id,
            "zone_name": self.zone_name,
            "step": self.step,
            "status": self.status,
            "message": self.message,
            "timestamp": int(self.timestamp * 1000)
        }


@dataclass
class PatrolState:
    """巡检状态"""
    is_active: bool = False
    current_step: PatrolStep = PatrolStep.IDLE
    progress: int = 0
    message: str = ""
    results: List[PatrolResult] = field(default_factory=list)
    
    def to_dict(self) -> dict:
        return {
            "is_active": self.is_active,
            "current_step": self.current_step.value,
            "progress": self.progress,
            "message": self.message,
            "results": [r.to_dict() for r in self.results[-20:]]  # 只返回最近20条结果
        }


class PatrolManager:
    """
    巡检管理器
    
    实现设备巡检功能，包括：
    - 设备自检（有/离人检测、动火状态检测）
    - 报警演示（屏蔽人形检测器，执行报警流程）
    - 强制动作（预警、报警、切电所有区）
    """
    
    _instance: Optional['PatrolManager'] = None
    
    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance
    
    def __init__(self):
        if hasattr(self, '_initialized'):
            return
        self._initialized = True
        
        self._logger = get_logger()
        self._state = PatrolState()
        self._lock = threading.Lock()
        
        # 回调函数 - 用于推送状态
        self._on_status_update: Optional[Callable[[dict], None]] = None
        
        # 演示模式标志 - 屏蔽人形检测器
        self._demo_mode = False
        
        self._logger.info("巡检管理器初始化完成")
    
    def set_status_callback(self, callback: Callable[[dict], None]):
        """设置状态更新回调"""
        self._on_status_update = callback
    
    def _broadcast_status(self, event_type: str = "status_update"):
        """广播当前状态"""
        try:
            from ..api.websocket import sync_broadcast_patrol_event
            sync_broadcast_patrol_event(event_type, self._state.to_dict())
        except Exception as e:
            self._logger.error(f"广播巡检状态失败: {e}")
    
    def _add_result(self, zone_id: str, zone_name: str, step: str, 
                    status: str, message: str):
        """添加巡检结果"""
        result = PatrolResult(
            zone_id=zone_id,
            zone_name=zone_name,
            step=step,
            status=status,
            message=message
        )
        with self._lock:
            self._state.results.append(result)
        
        self._broadcast_status("result")
    
    def _update_progress(self, step: PatrolStep, progress: int, message: str):
        """更新进度"""
        with self._lock:
            self._state.current_step = step
            self._state.progress = progress
            self._state.message = message
        
        self._broadcast_status()
    
    @property
    def is_active(self) -> bool:
        """检查是否在巡检模式"""
        with self._lock:
            return self._state.is_active
    
    @property
    def is_demo_mode(self) -> bool:
        """检查是否在演示模式（屏蔽人形检测器）"""
        return self._demo_mode
    
    def get_state(self) -> dict:
        """获取当前状态"""
        with self._lock:
            return self._state.to_dict()
    
    def start_patrol(self) -> dict:
        """开始巡检模式"""
        with self._lock:
            if self._state.is_active:
                return {"success": False, "message": "巡检模式已在进行中"}
            
            self._state.is_active = True
            self._state.current_step = PatrolStep.IDLE
            self._state.progress = 0
            self._state.message = "巡检模式已开启"
            self._state.results = []
        
        # 停止所有正在进行的播报
        voice_player.stop_playback()
        
        self._logger.info("巡检模式已开启")
        self._broadcast_status()
        
        return {"success": True, "message": "巡检模式已开启"}
    
    def stop_patrol(self) -> dict:
        """退出巡检模式"""
        with self._lock:
            self._state.is_active = False
            self._state.current_step = PatrolStep.IDLE
            self._state.progress = 0
            self._state.message = "巡检模式已退出"
        
        self._demo_mode = False
        
        self._logger.info("巡检模式已退出")
        self._broadcast_status()
        
        return {"success": True, "message": "巡检模式已退出"}
    
    def check_person_zone(self, zone_id: str) -> dict:
        """
        检测单个灶台的离人状态
        
        Args:
            zone_id: 灶台ID
            
        Returns:
            {"success": bool, "has_person": bool, "message": str}
        """
        if not self.is_active:
            return {"success": False, "message": "请先开启巡检模式"}
        
        from ..zone.state_machine import zone_manager
        
        sm = zone_manager.get_zone(zone_id)
        if not sm:
            return {"success": False, "message": f"灶台 '{zone_id}' 不存在"}
        
        zone_name = sm.zone.name
        has_person = sm.zone.has_person
        
        # 播放对应语音
        if has_person:
            audio_path = _get_audio_path(zone_id, "patrol_has_person")
            message = f"{zone_name}有人"
        else:
            audio_path = _get_audio_path(zone_id, "patrol_no_person")
            message = f"{zone_name}没人"
        
        if audio_path:
            voice_player.play_file(audio_path)
        
        # 添加结果
        self._add_result(zone_id, zone_name, "离人检测", "success", message)
        
        return {"success": True, "has_person": has_person, "message": message}
    
    def check_fire_zone(self, zone_id: str) -> dict:
        """
        检测单个灶台的动火状态
        
        Args:
            zone_id: 灶台ID
            
        Returns:
            {"success": bool, "is_fire_on": bool, "message": str}
        """
        if not self.is_active:
            return {"success": False, "message": "请先开启巡检模式"}
        
        from ..zone.state_machine import zone_manager
        from ..serial_port.serial_manager import serial_manager
        
        sm = zone_manager.get_zone(zone_id)
        if not sm:
            return {"success": False, "message": f"灶台 '{zone_id}' 不存在"}
        
        zone_name = sm.zone.name
        is_fire_on = serial_manager.is_fire_on(zone_id)
        
        # 播放对应语音
        if is_fire_on:
            audio_path = _get_audio_path(zone_id, "patrol_fire_on")
            message = f"{zone_name}动火"
        else:
            audio_path = _get_audio_path(zone_id, "patrol_no_fire")
            message = f"{zone_name}未动火"
        
        if audio_path:
            voice_player.play_file(audio_path)
        
        # 添加结果
        self._add_result(zone_id, zone_name, "动火检测", "success", message)
        
        return {"success": True, "is_fire_on": is_fire_on, "message": message}
    
    def alarm_demo_zone(self, zone_id: str) -> dict:
        """
        对单个灶台进行报警演示
        
        演示流程：预警 -> 10秒 -> 报警 -> 10秒 -> 切电
        前提条件：灶台必须处于动火状态
        
        Args:
            zone_id: 灶台ID
        """
        if not self.is_active:
            return {"success": False, "message": "请先开启巡检模式"}
        
        from ..zone.state_machine import zone_manager
        from ..serial_port.serial_manager import serial_manager
        
        sm = zone_manager.get_zone(zone_id)
        if not sm:
            return {"success": False, "message": f"灶台 '{zone_id}' 不存在"}
        
        zone_name = sm.zone.name
        
        # 检查是否动火
        if not serial_manager.is_fire_on(zone_id):
            self._add_result(zone_id, zone_name, "报警演示", "error", f"{zone_name}未动火，无法演示")
            return {"success": False, "message": f"{zone_name}未动火，请先开启灶台"}
        
        # 在后台线程执行演示
        thread = threading.Thread(
            target=self._run_alarm_demo_zone, 
            args=(zone_id, zone_name), 
            daemon=True
        )
        thread.start()
        
        return {"success": True, "message": f"{zone_name}报警演示已启动"}
    
    def _run_alarm_demo_zone(self, zone_id: str, zone_name: str):
        """执行单灶台报警演示（后台线程）"""
        import time
        
        # 开启演示模式
        self._demo_mode = True
        self._update_progress(PatrolStep.ALARM_DEMO, 10, f"{zone_name}报警演示开始...")
        
        try:
            # 预警
            self._update_progress(PatrolStep.ALARM_DEMO, 25, f"{zone_name}预警中...")
            audio_path = _get_audio_path(zone_id, "warning")
            if audio_path:
                voice_player.play_file(audio_path, priority=True)
            self._add_result(zone_id, zone_name, "报警演示", "warning", "预警")
            time.sleep(10)
            
            # 报警
            self._update_progress(PatrolStep.ALARM_DEMO, 50, f"{zone_name}报警中...")
            audio_path = _get_audio_path(zone_id, "alarm")
            if audio_path:
                voice_player.play_file(audio_path, priority=True)
            self._add_result(zone_id, zone_name, "报警演示", "warning", "报警")
            time.sleep(10)
            
            # 切电
            self._update_progress(PatrolStep.ALARM_DEMO, 75, f"{zone_name}切电中...")
            audio_path = _get_audio_path(zone_id, "action")
            if audio_path:
                voice_player.play_file(audio_path, priority=True)
            
            # 执行切电
            from ..serial_port.serial_manager import serial_manager
            serial_manager.cutoff(zone_id)
            
            self._add_result(zone_id, zone_name, "报警演示", "success", "切电完成")
            
        finally:
            self._demo_mode = False
        
        self._update_progress(PatrolStep.IDLE, 100, f"{zone_name}报警演示完成")
    
    def cutoff_zone(self, zone_id: str) -> dict:
        """
        对单个灶台执行切电
        
        Args:
            zone_id: 灶台ID
        """
        if not self.is_active:
            return {"success": False, "message": "请先开启巡检模式"}
        
        from ..zone.state_machine import zone_manager
        from ..serial_port.serial_manager import serial_manager
        
        sm = zone_manager.get_zone(zone_id)
        if not sm:
            return {"success": False, "message": f"灶台 '{zone_id}' 不存在"}
        
        zone_name = sm.zone.name
        
        # 播放切电语音
        audio_path = _get_audio_path(zone_id, "action")
        if audio_path:
            voice_player.play_file(audio_path, priority=True)
        
        # 执行切电
        serial_manager.cutoff(zone_id)
        
        self._add_result(zone_id, zone_name, "强制切电", "success", f"{zone_name}已切电")
        
        return {"success": True, "message": f"{zone_name}已切电"}
    
    def device_self_check(self) -> dict:
        """
        设备自检
        
        检查每个区的有/离人状态和动火状态，并进行语音提示
        """
        if not self.is_active:
            return {"success": False, "message": "请先开启巡检模式"}
        
        # 在后台线程执行自检
        thread = threading.Thread(target=self._run_self_check, daemon=True)
        thread.start()
        
        return {"success": True, "message": "设备自检已启动"}
    
    def _run_self_check(self):
        """执行设备自检（后台线程）"""
        from ..zone.state_machine import zone_manager
        from ..serial_port.serial_manager import serial_manager
        
        zones = zone_manager.get_all_zones()
        if not zones:
            self._update_progress(PatrolStep.IDLE, 100, "没有配置灶台，自检完成")
            return
        
        total_steps = len(zones) * 2  # 每个区域两步：离人检测和动火检测
        current_step = 0
        
        # 第一阶段：离人检测
        self._update_progress(PatrolStep.SELF_CHECK_PERSON, 0, "开始离人检测...")
        
        for i, sm in enumerate(zones):
            zone_id = sm.zone.id
            zone_name = sm.zone.name
            has_person = sm.zone.has_person
            
            current_step += 1
            progress = int((current_step / total_steps) * 100)
            
            # 播放对应语音
            if has_person:
                self._update_progress(
                    PatrolStep.SELF_CHECK_PERSON, 
                    progress, 
                    f"检测到{zone_name}有人"
                )
                # 播放"有人"语音
                audio_path = _get_audio_path(zone_id, "patrol_has_person")
                if audio_path:
                    voice_player.play_file(audio_path)
                    time.sleep(2)  # 等待语音播放
                
                # 提示人员离开
                self._add_result(zone_id, zone_name, "离人检测", "warning", 
                               f"{zone_name}检测到有人，请人员离开")
                
                # 等待几秒再次检测
                time.sleep(3)
                
                # 更新检测状态
                has_person = sm.zone.has_person
                if not has_person:
                    # 播放"人员离开"语音
                    audio_path = _get_audio_path(zone_id, "patrol_no_person")
                    if audio_path:
                        voice_player.play_file(audio_path)
                        time.sleep(1.5)
            else:
                self._update_progress(
                    PatrolStep.SELF_CHECK_PERSON, 
                    progress, 
                    f"{zone_name}离人状态正常"
                )
            
            self._add_result(zone_id, zone_name, "离人检测", "success", 
                           f"{zone_name}离人状态正常")
        
        # 第二阶段：动火检测
        self._update_progress(PatrolStep.SELF_CHECK_FIRE, 50, "开始动火检测...")
        
        for i, sm in enumerate(zones):
            zone_id = sm.zone.id
            zone_name = sm.zone.name
            # 使用 serial_manager 获取实际的动火状态
            is_fire_on = serial_manager.is_fire_on(zone_id)
            
            current_step += 1
            progress = int((current_step / total_steps) * 100)
            
            if is_fire_on:
                self._update_progress(
                    PatrolStep.SELF_CHECK_FIRE, 
                    progress, 
                    f"检测到{zone_name}动火中"
                )
                # 播放"动火中"语音
                audio_path = _get_audio_path(zone_id, "patrol_fire_on")
                if audio_path:
                    voice_player.play_file(audio_path)
                    time.sleep(2)
                
                # 提示关火
                self._add_result(zone_id, zone_name, "动火检测", "warning", 
                               f"{zone_name}动火中，请关火")
                
                # 等待几秒再次检测
                time.sleep(5)
                
                # 更新检测状态
                is_fire_on = serial_manager.is_fire_on(zone_id)
                if not is_fire_on:
                    # 播放"未动火"语音
                    audio_path = _get_audio_path(zone_id, "patrol_no_fire")
                    if audio_path:
                        voice_player.play_file(audio_path)
                        time.sleep(1.5)
            else:
                self._update_progress(
                    PatrolStep.SELF_CHECK_FIRE, 
                    progress, 
                    f"{zone_name}动火状态正常"
                )
            
            self._add_result(zone_id, zone_name, "动火检测", "success", 
                           f"{zone_name}动火状态正常")
        
        self._update_progress(PatrolStep.IDLE, 100, "设备自检完成")
    
    def alarm_demo(self) -> dict:
        """
        报警演示
        
        屏蔽人形检测器信号，在动火状态下执行完整报警流程演示
        """
        if not self.is_active:
            return {"success": False, "message": "请先开启巡检模式"}
        
        # 在后台线程执行演示
        thread = threading.Thread(target=self._run_alarm_demo, daemon=True)
        thread.start()
        
        return {"success": True, "message": "报警演示已启动"}
    
    def _run_alarm_demo(self):
        """执行报警演示（后台线程）"""
        from ..zone.state_machine import zone_manager
        from ..zone.models import ZoneState
        from ..serial_port.serial_manager import serial_manager
        
        # 开启演示模式（屏蔽人形检测）
        self._demo_mode = True
        self._update_progress(PatrolStep.ALARM_DEMO, 10, "报警演示开始，已屏蔽人形检测器...")
        
        zones = zone_manager.get_all_zones()
        if not zones:
            self._demo_mode = False
            self._update_progress(PatrolStep.IDLE, 100, "没有配置灶台，演示结束")
            return
        
        # 找到第一个动火的灶台进行演示
        demo_zone = None
        for sm in zones:
            zone_id = sm.zone.id
            if serial_manager.is_fire_on(zone_id):
                demo_zone = sm
                break
        
        if not demo_zone:
            self._demo_mode = False
            self._update_progress(PatrolStep.IDLE, 100, "没有检测到动火灶台，请先开启灶台")
            self._add_result("", "", "报警演示", "error", "没有检测到动火灶台")
            return
        
        zone_id = demo_zone.zone.id
        zone_name = demo_zone.zone.name
        
        try:
            # 演示预警
            self._update_progress(PatrolStep.ALARM_DEMO, 30, f"正在演示{zone_name}预警...")
            audio_path = _get_audio_path(zone_id, "warning")
            if audio_path:
                voice_player.play_file(audio_path, priority=True)
            self._add_result(zone_id, zone_name, "报警演示", "warning", "预警演示")
            time.sleep(5)
            
            # 演示报警
            self._update_progress(PatrolStep.ALARM_DEMO, 60, f"正在演示{zone_name}报警...")
            audio_path = _get_audio_path(zone_id, "alarm")
            if audio_path:
                voice_player.play_file(audio_path, priority=True)
            self._add_result(zone_id, zone_name, "报警演示", "warning", "报警演示")
            time.sleep(5)
            
            # 演示切电
            self._update_progress(PatrolStep.ALARM_DEMO, 90, f"正在演示{zone_name}切电...")
            audio_path = _get_audio_path(zone_id, "action")
            if audio_path:
                voice_player.play_file(audio_path, priority=True)
            # 模拟切电（不实际执行）
            self._add_result(zone_id, zone_name, "报警演示", "success", "切电演示（模拟）")
            time.sleep(3)
            
        finally:
            # 关闭演示模式
            self._demo_mode = False
        
        self._update_progress(PatrolStep.IDLE, 100, "报警演示完成")
    
    def force_warning_all(self) -> dict:
        """强制预警（所有区）"""
        if not self.is_active:
            return {"success": False, "message": "请先开启巡检模式"}
        
        thread = threading.Thread(target=self._run_force_warning, daemon=True)
        thread.start()
        
        return {"success": True, "message": "强制预警已触发"}
    
    def _run_force_warning(self):
        """执行强制预警"""
        from ..zone.state_machine import zone_manager
        from ..zone.models import ZoneState
        
        self._update_progress(PatrolStep.FORCE_WARNING, 10, "正在触发所有区预警...")
        
        zones = zone_manager.get_all_zones()
        for i, sm in enumerate(zones):
            zone_id = sm.zone.id
            zone_name = sm.zone.name
            
            progress = int(((i + 1) / len(zones)) * 100)
            self._update_progress(PatrolStep.FORCE_WARNING, progress, 
                                f"触发{zone_name}预警...")
            
            # 播放预警语音
            audio_path = _get_audio_path(zone_id, "warning")
            if audio_path:
                voice_player.play_file(audio_path)
            
            self._add_result(zone_id, zone_name, "强制预警", "warning",
                           f"{zone_name}预警已触发")

            # 上报到远程服务器
            sync_upload_alarm_record(
                zone_id=zone_id,
                zone_name=zone_name,
                alarm_type="warning",
                image_base64=None,
                message=f"{zone_name} 强制预警"
            )
            time.sleep(2)
        
        self._update_progress(PatrolStep.IDLE, 100, "所有区预警已触发")
    
    def force_alarm_all(self) -> dict:
        """强制报警（所有区）"""
        if not self.is_active:
            return {"success": False, "message": "请先开启巡检模式"}
        
        thread = threading.Thread(target=self._run_force_alarm, daemon=True)
        thread.start()
        
        return {"success": True, "message": "强制报警已触发"}
    
    def _run_force_alarm(self):
        """执行强制报警"""
        from ..zone.state_machine import zone_manager
        
        self._update_progress(PatrolStep.FORCE_ALARM, 10, "正在触发所有区报警...")
        
        zones = zone_manager.get_all_zones()
        for i, sm in enumerate(zones):
            zone_id = sm.zone.id
            zone_name = sm.zone.name
            
            progress = int(((i + 1) / len(zones)) * 100)
            self._update_progress(PatrolStep.FORCE_ALARM, progress, 
                                f"触发{zone_name}报警...")
            
            # 播放报警语音
            audio_path = _get_audio_path(zone_id, "alarm")
            if audio_path:
                voice_player.play_file(audio_path)
            
            self._add_result(zone_id, zone_name, "强制报警", "warning",
                           f"{zone_name}报警已触发")

            # 上报到远程服务器
            sync_upload_alarm_record(
                zone_id=zone_id,
                zone_name=zone_name,
                alarm_type="alarm",
                image_base64=None,
                message=f"{zone_name} 强制报警"
            )
            time.sleep(2)
        
        self._update_progress(PatrolStep.IDLE, 100, "所有区报警已触发")
    
    def force_cutoff_all(self) -> dict:
        """强制切电（所有区）"""
        if not self.is_active:
            return {"success": False, "message": "请先开启巡检模式"}
        
        thread = threading.Thread(target=self._run_force_cutoff, daemon=True)
        thread.start()
        
        return {"success": True, "message": "强制切电已触发"}
    
    def _run_force_cutoff(self):
        """执行强制切电"""
        from ..zone.state_machine import zone_manager
        from ..serial_port.serial_manager import serial_manager
        
        self._update_progress(PatrolStep.FORCE_CUTOFF, 10, "正在触发所有区切电...")
        
        zones = zone_manager.get_all_zones()
        for i, sm in enumerate(zones):
            zone_id = sm.zone.id
            zone_name = sm.zone.name
            
            progress = int(((i + 1) / len(zones)) * 100)
            self._update_progress(PatrolStep.FORCE_CUTOFF, progress, 
                                f"触发{zone_name}切电...")
            
            # 播放切电语音
            audio_path = _get_audio_path(zone_id, "action")
            if audio_path:
                voice_player.play_file(audio_path)
            
            # 执行切电 - 使用 serial_manager
            serial_manager.cutoff(zone_id)
            
            self._add_result(zone_id, zone_name, "强制切电", "success",
                           f"{zone_name}已切电")

            # 上报到远程服务器
            sync_upload_alarm_record(
                zone_id=zone_id,
                zone_name=zone_name,
                alarm_type="cutoff",
                image_base64=None,
                message=f"{zone_name} 强制切电"
            )
            time.sleep(2)
        
        self._update_progress(PatrolStep.IDLE, 100, "所有区切电已完成")


# 全局巡检管理器实例
patrol_manager = PatrolManager()
