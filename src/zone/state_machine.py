"""
灶台状态机
管理每个灶台的状态转换和定时器逻辑
"""
import time
import threading
from typing import Dict, Optional, Callable, List
from dataclasses import dataclass
import numpy as np

from .models import Zone, ZoneState
from ..utils.config import ZoneConfig, get_config
from ..utils.logger import get_logger, event_logger


@dataclass
class StateChangeEvent:
    """状态变化事件"""
    zone_id: str
    zone_name: str
    old_state: ZoneState
    new_state: ZoneState
    timestamp: float
    message: str


class ZoneStateMachine:
    """灶台状态机"""
    
    def __init__(self, zone_config: ZoneConfig):
        """
        初始化状态机
        
        Args:
            zone_config: 灶台配置
        """
        self.zone = Zone(
            id=zone_config.id,
            name=zone_config.name,
            camera_id=zone_config.camera_id,
            roi=zone_config.roi,
            enabled=zone_config.enabled
        )
        
        self._logger = get_logger()
        self._lock = threading.Lock()
        
        # 回调函数
        self._on_warning: Optional[Callable[[Zone], None]] = None
        self._on_alarm: Optional[Callable[[Zone], None]] = None
        self._on_cutoff: Optional[Callable[[Zone], None]] = None
        self._on_state_change: Optional[Callable[[StateChangeEvent], None]] = None
        self._on_temp_alarm: Optional[Callable[[Zone, float, np.ndarray], None]] = None  # 温度报警回调（含帧参数）
        
        # 时间追踪
        self._no_person_start_time: Optional[float] = None
        self._last_update_time = time.time()
    
    def set_callbacks(self, 
                      on_warning: Optional[Callable[[Zone], None]] = None,
                      on_alarm: Optional[Callable[[Zone], None]] = None,
                      on_cutoff: Optional[Callable[[Zone], None]] = None,
                      on_state_change: Optional[Callable[[StateChangeEvent], None]] = None,
                      on_temp_alarm: Optional[Callable[[Zone, float, np.ndarray], None]] = None):
        """设置回调函数"""
        self._on_warning = on_warning
        self._on_alarm = on_alarm
        self._on_cutoff = on_cutoff
        self._on_state_change = on_state_change
        self._on_temp_alarm = on_temp_alarm
    
    def update(self, has_person: bool, is_fire_on: bool, 
               current_frame: Optional[np.ndarray] = None,
               temperature: Optional[float] = None) -> ZoneState:
        """
        更新状态机
        
        三阶段报警逻辑：
        1. WARNING (预警): warning_time 秒后触发
        2. ALARM (报警): alarm_time 秒后触发
        3. CUTOFF (切电): action_time 秒后触发
        
        温度报警逻辑：
        - 条件：温度超阈值 + 离人 → 触发 TEMP_ALARM
        """
        with self._lock:
            # 获取全局配置 - 使用三阶段报警配置
            config = get_config()
            alarm_config = config.alarm
            warning_time = alarm_config.warning_time
            alarm_time = alarm_config.alarm_time
            action_time = alarm_config.action_time
            temp_threshold = alarm_config.temp_alarm_threshold
            
            current_time = time.time()
            dt = current_time - self._last_update_time
            self._last_update_time = current_time
            
            old_state = self.zone.state
            self.zone.has_person = has_person
            self.zone.is_fire_on = is_fire_on
            
            # 更新温度值
            if temperature is not None:
                self.zone.temperature = temperature
            
            # 温度报警检查 (仅当有温度值且离人时)
            temp_alarm_triggered = False
            if temperature is not None and not has_person:
                if temperature > temp_threshold:
                    if old_state != ZoneState.TEMP_ALARM:
                        self._logger.warning(f"温度超阈值 ({temperature:.1f}°C > {temp_threshold}°C)，触发温度报警")
                        self._transition_to(ZoneState.TEMP_ALARM)
                        # 触发温度报警回调，传递当前帧
                        if self._on_temp_alarm:
                            self._on_temp_alarm(self.zone, temperature, current_frame)
                    temp_alarm_triggered = True
            
            # 如果已触发温度报警，跳过其他状态判断
            if temp_alarm_triggered:
                return self.zone.state
            
            # 温度报警状态复位：检测到人或温度恢复正常
            if self.zone.state == ZoneState.TEMP_ALARM:
                if has_person:
                    self._logger.info("检测到人员回场，温度报警复位")
                    self._transition_to(ZoneState.IDLE)
                    self._reset_timers()
                elif temperature is not None and temperature <= temp_threshold:
                    self._logger.info("温度恢复正常，温度报警复位")
                    self._transition_to(ZoneState.IDLE)
                    self._reset_timers()
                return self.zone.state
            
            # 状态机逻辑
            if not is_fire_on:
                # 未开火 -> 空闲状态（硬件复位）
                self._transition_to(ZoneState.IDLE)
                self._reset_timers()
            
            elif self.zone.state == ZoneState.CUTOFF:
                # 切电状态复位逻辑
                # 条件1: 检测到人自动复位
                if has_person:
                    self._trigger_cutoff_reset("检测到人员回场")
                # 条件2: 切电后10秒电流值大于阈值自动复位（通过串口管理器判断）
                elif self._check_current_reset():
                    self._trigger_cutoff_reset("电流值恢复正常")
            
            elif has_person:
                # 开火 + 有人 -> 有人动火
                self._transition_to(ZoneState.ACTIVE_WITH_PERSON)
                self._reset_timers()
            
            else:
                # 开火 + 无人
                if self._no_person_start_time is None:
                    self._no_person_start_time = current_time
                
                no_person_duration = current_time - self._no_person_start_time
                self.zone.no_person_duration = no_person_duration
                
                # 计算倒计时（使用三阶段时间）
                self.zone.warning_remaining = max(0, warning_time - no_person_duration)
                self.zone.alarm_remaining = max(0, alarm_time - no_person_duration)
                self.zone.cutoff_remaining = max(0, action_time - no_person_duration)
                
                if no_person_duration >= action_time:
                    # 阶段3：超过切电时间
                    if old_state != ZoneState.CUTOFF:
                        self._logger.info(f"无人时间超过切电时间 ({action_time}秒)，切换到CUTOFF状态")
                        self._transition_to(ZoneState.CUTOFF)
                        # 保存截图
                        if current_frame is not None:
                            self._save_cutoff_snapshot(current_frame)
                        # 触发切电回调
                        if self._on_cutoff:
                            self._on_cutoff(self.zone, current_frame)
                
                elif no_person_duration >= alarm_time:
                    # 阶段2：超过报警时间
                    if old_state not in [ZoneState.ALARM, ZoneState.CUTOFF]:
                        self._logger.info(f"无人时间超过报警时间 ({alarm_time}秒)，切换到ALARM状态")
                        self._transition_to(ZoneState.ALARM)
                        # 保存报警截图
                        if current_frame is not None:
                            self._save_alarm_snapshot(current_frame)
                        # 触发报警回调
                        if self._on_alarm:
                            self._on_alarm(self.zone, current_frame)
                
                elif no_person_duration >= warning_time:
                    # 阶段1：超过预警时间
                    if old_state not in [ZoneState.WARNING, ZoneState.ALARM, ZoneState.CUTOFF]:
                        self._logger.info(f"无人时间超过预警时间 ({warning_time}秒)，切换到WARNING状态")
                        self._transition_to(ZoneState.WARNING)
                        # 保存预警截图
                        if current_frame is not None:
                            self._save_warning_snapshot(current_frame)
                        # 触发预警回调
                        if self._on_warning:
                            self._on_warning(self.zone, current_frame)
                
                else:
                    # 计时中
                    if old_state not in [ZoneState.ACTIVE_NO_PERSON, ZoneState.WARNING, ZoneState.ALARM, ZoneState.CUTOFF]:
                        self._transition_to(ZoneState.ACTIVE_NO_PERSON)
            
            return self.zone.state
    
    def _transition_to(self, new_state: ZoneState):
        """状态转换"""
        if self.zone.state != new_state:
            old_state = self.zone.state
            self.zone.state = new_state
            
            message = f"状态变化: {old_state.value} -> {new_state.value}"
            self._logger.info(f"[{self.zone.id}] {self.zone.name} {message}")
            
            # 触发状态变化回调
            if self._on_state_change:
                event = StateChangeEvent(
                    zone_id=self.zone.id,
                    zone_name=self.zone.name,
                    old_state=old_state,
                    new_state=new_state,
                    timestamp=time.time(),
                    message=message
                )
                self._on_state_change(event)
    
    def _reset_timers(self):
        """重置定时器"""
        self._no_person_start_time = None
        self.zone.no_person_duration = 0.0
        self.zone.warning_remaining = 0.0
        self.zone.cutoff_remaining = 0.0
    
    def _check_current_reset(self) -> bool:
        """
        检查是否可以通过电流值复位
        
        条件：切电后10秒 且 电流值大于阈值
        """
        try:
            from ..serial_port.serial_manager import serial_manager
            return serial_manager.can_reset_by_current(self.zone.id)
        except Exception:
            return False
    
    def _trigger_cutoff_reset(self, reason: str):
        """
        触发切电复位
        
        Args:
            reason: 复位原因
        """
        old_state = self.zone.state
        self._reset_timers()
        self.zone.state = ZoneState.IDLE
        
        # 清除串口管理器中的切电状态
        try:
            from ..serial_port.serial_manager import serial_manager
            serial_manager.clear_cutoff_state(self.zone.id)
        except Exception:
            pass
        
        event_logger.log_reset(self.zone.id, f"自动复位: {reason}")
        self._logger.info(f"[{self.zone.id}] 自动复位: {reason}")
        
        if self._on_state_change:
            event = StateChangeEvent(
                zone_id=self.zone.id,
                zone_name=self.zone.name,
                old_state=old_state,
                new_state=ZoneState.IDLE,
                timestamp=time.time(),
                message=f"自动复位: {reason}"
            )
            self._on_state_change(event)
    
    def _save_cutoff_snapshot(self, frame: np.ndarray):
        """保存切电截图"""
        path = event_logger.save_snapshot(self.zone.id, frame, "cutoff")
        if path:
            self.zone.last_snapshot_path = path
            event_logger.log_cutoff(self.zone.id, f"无人看管超时，已切电。截图: {path}")
    
    def _save_warning_snapshot(self, frame: np.ndarray):
        """保存预警截图"""
        path = event_logger.save_snapshot(self.zone.id, frame, "warning")
        if path:
            self.zone.last_snapshot_path = path
            event_logger.log_warning(self.zone.id, f"无人看管即将超时。截图: {path}")
    
    def _save_alarm_snapshot(self, frame: np.ndarray):
        """保存报警截图"""
        path = event_logger.save_snapshot(self.zone.id, frame, "alarm")
        if path:
            self.zone.last_snapshot_path = path
            event_logger.log_warning(self.zone.id, f"无人看管已超时报警。截图: {path}")
    
    def force_idle(self):
        """
        强制重置到空闲状态（用于灶台停用时）
        
        会重置所有定时器并将状态设置为 IDLE，同时触发状态变化回调
        """
        with self._lock:
            if self.zone.state == ZoneState.IDLE:
                return  # 已经是空闲状态，无需操作
            
            old_state = self.zone.state
            self._reset_timers()
            self.zone.state = ZoneState.IDLE
            
            self._logger.info(f"[{self.zone.id}] 强制重置: {old_state.value} -> idle")
            
            # 触发状态变化回调
            if self._on_state_change:
                event = StateChangeEvent(
                    zone_id=self.zone.id,
                    zone_name=self.zone.name,
                    old_state=old_state,
                    new_state=ZoneState.IDLE,
                    timestamp=time.time(),
                    message="灶台停用，强制重置"
                )
                self._on_state_change(event)
    
    def reset(self) -> bool:
        """
        复位状态（人工介入后调用）
        """
        with self._lock:
            if self.zone.state in [ZoneState.WARNING, ZoneState.CUTOFF]:
                old_state = self.zone.state
                self._reset_timers()
                self.zone.state = ZoneState.IDLE
                
                event_logger.log_reset(self.zone.id, f"手动复位，从 {old_state.value} 恢复到空闲")
                
                if self._on_state_change:
                    event = StateChangeEvent(
                        zone_id=self.zone.id,
                        zone_name=self.zone.name,
                        old_state=old_state,
                        new_state=ZoneState.IDLE,
                        timestamp=time.time(),
                        message="手动复位"
                    )
                    self._on_state_change(event)
                
                return True
        return False
    
    def set_fire_state(self, is_on: bool):
        """设置开火状态（用于模拟GPIO）"""
        with self._lock:
            self.zone.is_fire_on = is_on
            self._logger.info(f"[{self.zone.id}] 开火状态设置为: {'开' if is_on else '关'}")
    
    def update_config(self, roi: List = None):
        """更新配置"""
        with self._lock:
            if roi is not None:
                self.zone.roi = [tuple(p) for p in roi]
    
    def get_status(self) -> dict:
        """获取当前状态（实时计算倒计时）"""
        with self._lock:
            # 如果当前状态是无人动火相关状态，需要实时计算倒计时
            if self._no_person_start_time is not None and self.zone.state in [
                ZoneState.ACTIVE_NO_PERSON, ZoneState.WARNING, ZoneState.ALARM
            ]:
                # 获取三阶段报警配置
                config = get_config()
                alarm_config = config.alarm
                warning_time = alarm_config.warning_time
                alarm_time = alarm_config.alarm_time
                action_time = alarm_config.action_time
                
                # 计算实时的无人持续时间
                current_time = time.time()
                no_person_duration = current_time - self._no_person_start_time
                
                # 实时更新倒计时值
                self.zone.no_person_duration = no_person_duration
                self.zone.warning_remaining = max(0, warning_time - no_person_duration)
                self.zone.alarm_remaining = max(0, alarm_time - no_person_duration)
                self.zone.cutoff_remaining = max(0, action_time - no_person_duration)
            
            return self.zone.to_dict()


class ZoneManager:
    """灶台管理器"""
    
    _instance: Optional['ZoneManager'] = None
    
    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance
    
    def __init__(self):
        if hasattr(self, '_initialized'):
            return
        self._initialized = True
        self._zones: Dict[str, ZoneStateMachine] = {}
        self._fire_states: Dict[str, bool] = {}  # 模拟GPIO开火状态
        self._logger = get_logger()
    
    def add_zone(self, config: ZoneConfig, 
                 on_warning: Callable = None,
                 on_alarm: Callable = None,
                 on_cutoff: Callable = None,
                 on_state_change: Callable = None,
                 on_temp_alarm: Callable = None) -> ZoneStateMachine:
        """添加灶台"""
        sm = ZoneStateMachine(config)
        sm.set_callbacks(on_warning, on_alarm, on_cutoff, on_state_change, on_temp_alarm)
        self._zones[config.id] = sm
        self._fire_states[config.id] = False  # 默认关火
        self._logger.info(f"添加灶台: {config.id} ({config.name})")
        return sm
    
    def get_zone(self, zone_id: str) -> Optional[ZoneStateMachine]:
        """获取灶台状态机"""
        return self._zones.get(zone_id)
    
    def get_all_zones(self) -> List[ZoneStateMachine]:
        """获取所有灶台"""
        return list(self._zones.values())
    
    def update_zone(self, zone_id: str, has_person: bool, 
                    current_frame: np.ndarray = None) -> Optional[ZoneState]:
        """更新灶台状态"""
        sm = self._zones.get(zone_id)
        if sm:
            # 从 serial_manager 获取实际的动火状态和温度
            is_fire_on = self._fire_states.get(zone_id, False)
            temperature = None
            try:
                from ..serial_port.serial_manager import serial_manager
                is_fire_on = serial_manager.is_fire_on(zone_id)
                temperature = serial_manager.get_temperature(zone_id)
                if temperature == 0.0:
                    temperature = None  # 0.0 表示未绑定或未读取到
            except Exception:
                pass
            return sm.update(has_person, is_fire_on, current_frame, temperature)
        return None
    
    def set_fire_state(self, zone_id: str, is_on: bool):
        """设置开火状态（模拟GPIO）"""
        self._fire_states[zone_id] = is_on
        sm = self._zones.get(zone_id)
        if sm:
            sm.set_fire_state(is_on)
    
    def get_fire_state(self, zone_id: str) -> bool:
        """获取开火状态"""
        return self._fire_states.get(zone_id, False)
    
    def reset_zone(self, zone_id: str) -> bool:
        """复位灶台"""
        sm = self._zones.get(zone_id)
        if sm:
            return sm.reset()
        return False
    
    def get_all_status(self) -> List[dict]:
        """获取所有灶台状态"""
        # 获取电流值和温度值
        currents = {}
        temperatures = {}
        try:
            from ..serial_port.serial_manager import serial_manager
            currents = serial_manager.get_all_currents()
            temperatures = serial_manager.get_all_temperatures()
        except Exception:
            pass
            
        result = []
        for sm in self._zones.values():
            status = sm.get_status()
            # 注入实时电流值和温度值
            status['current_value'] = currents.get(sm.zone.id, 0)
            status['temperature'] = round(temperatures.get(sm.zone.id, 0.0), 1)
            result.append(status)
            
        return result
    
    def initialize_from_config(self, zones: List[ZoneConfig],
                              on_warning: Callable = None,
                              on_alarm: Callable = None,
                              on_cutoff: Callable = None,
                              on_state_change: Callable = None,
                              on_temp_alarm: Callable = None):
        """从配置初始化"""
        for config in zones:
            self.add_zone(config, on_warning, on_alarm, on_cutoff, on_state_change, on_temp_alarm)
        self._logger.info(f"从配置加载了 {len(zones)} 个灶台")


# 全局灶台管理器
zone_manager = ZoneManager()
