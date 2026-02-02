"""
测试 zone/state_machine.py 模块

测试内容:
- 状态转换逻辑
- 三阶段报警（预警→报警→切电）
- 回调触发
- 手动复位
"""
import pytest
import time
from unittest.mock import MagicMock, patch
from dataclasses import dataclass
from typing import List, Tuple


# ==================== Mock 类 ====================

@dataclass
class MockAlarmConfig:
    warning_time: int = 5  # 使用较短时间便于测试
    alarm_time: int = 10
    action_time: int = 15
    broadcast_interval: int = 5
    warning_message: str = "预警"
    alarm_message: str = "报警"
    action_message: str = "切电"
    temp_alarm_threshold: float = 80.0
    temp_alarm_message: str = "温度过高"


@dataclass
class MockAppConfig:
    alarm: MockAlarmConfig = None
    
    def __post_init__(self):
        if self.alarm is None:
            self.alarm = MockAlarmConfig()


@dataclass
class MockZoneConfig:
    id: str
    name: str
    camera_id: str
    roi: List[Tuple[float, float]]
    enabled: bool = True
    serial_index: int = 0
    fire_current_threshold: int = 100


# ==================== 测试类 ====================

class TestZoneStateMachine:
    """测试 ZoneStateMachine 类"""
    
    @pytest.fixture
    def mock_dependencies(self):
        """Mock 所有外部依赖"""
        mock_config = MockAppConfig()
        mock_logger = MagicMock()
        mock_event_logger = MagicMock()
        mock_event_logger.save_snapshot = MagicMock(return_value="/mock/snapshot.jpg")
        
        with patch('src.zone.state_machine.get_config', return_value=mock_config), \
             patch('src.zone.state_machine.get_logger', return_value=mock_logger), \
             patch('src.zone.state_machine.event_logger', mock_event_logger):
            yield {
                'config': mock_config,
                'logger': mock_logger,
                'event_logger': mock_event_logger
            }
    
    @pytest.fixture
    def state_machine(self, mock_dependencies):
        """创建状态机实例"""
        from src.zone.state_machine import ZoneStateMachine
        
        config = MockZoneConfig(
            id="zone_1",
            name="测试灶台",
            camera_id="cam_1",
            roi=[(0.1, 0.1), (0.9, 0.9)]
        )
        return ZoneStateMachine(config)
    
    def test_initial_state_is_idle(self, state_machine):
        """测试初始状态为 IDLE"""
        from src.zone.models import ZoneState
        assert state_machine.zone.state == ZoneState.IDLE
    
    def test_fire_off_stays_idle(self, state_machine):
        """测试未开火时保持空闲"""
        from src.zone.models import ZoneState
        
        # 无论有没有人，未开火都应该是 IDLE
        state_machine.update(has_person=True, is_fire_on=False)
        assert state_machine.zone.state == ZoneState.IDLE
        
        state_machine.update(has_person=False, is_fire_on=False)
        assert state_machine.zone.state == ZoneState.IDLE
    
    def test_fire_on_with_person(self, state_machine):
        """测试开火+有人 → ACTIVE_WITH_PERSON"""
        from src.zone.models import ZoneState
        
        state_machine.update(has_person=True, is_fire_on=True)
        assert state_machine.zone.state == ZoneState.ACTIVE_WITH_PERSON
    
    def test_fire_on_no_person_starts_countdown(self, state_machine):
        """测试开火+无人开始计时"""
        from src.zone.models import ZoneState
        
        state_machine.update(has_person=False, is_fire_on=True)
        assert state_machine.zone.state == ZoneState.ACTIVE_NO_PERSON
        assert state_machine.zone.no_person_duration >= 0
    
    def test_person_returns_resets_state(self, state_machine):
        """测试人员回场重置状态"""
        from src.zone.models import ZoneState
        
        # 先进入无人状态
        state_machine.update(has_person=False, is_fire_on=True)
        assert state_machine.zone.state == ZoneState.ACTIVE_NO_PERSON
        
        # 人员回场
        state_machine.update(has_person=True, is_fire_on=True)
        assert state_machine.zone.state == ZoneState.ACTIVE_WITH_PERSON
        assert state_machine.zone.no_person_duration == 0.0


class TestStateTransitions:
    """测试状态转换"""
    
    @pytest.fixture
    def mock_deps_fast(self):
        """带快速超时的 mock 依赖"""
        mock_config = MockAppConfig(
            alarm=MockAlarmConfig(
                warning_time=1,
                alarm_time=2,
                action_time=3
            )
        )
        mock_logger = MagicMock()
        mock_event_logger = MagicMock()
        mock_event_logger.save_snapshot = MagicMock(return_value="/mock/snap.jpg")
        
        with patch('src.zone.state_machine.get_config', return_value=mock_config), \
             patch('src.zone.state_machine.get_logger', return_value=mock_logger), \
             patch('src.zone.state_machine.event_logger', mock_event_logger):
            yield {
                'config': mock_config,
                'logger': mock_logger,
                'event_logger': mock_event_logger
            }
    
    @pytest.fixture
    def fast_sm(self, mock_deps_fast):
        """创建快速超时的状态机"""
        from src.zone.state_machine import ZoneStateMachine
        
        config = MockZoneConfig(
            id="zone_fast",
            name="快速灶台",
            camera_id="cam_1",
            roi=[(0, 0), (1, 1)]
        )
        return ZoneStateMachine(config)
    
    def test_warning_callback(self, fast_sm):
        """测试预警回调触发"""
        from src.zone.models import ZoneState
        
        callback = MagicMock()
        fast_sm.set_callbacks(on_warning=callback)
        
        # 模拟时间流逝到达预警阈值但未到报警阈值
        # warning_time=1, alarm_time=2, 设置1.5秒
        fast_sm._no_person_start_time = time.time() - 1.5
        fast_sm.update(has_person=False, is_fire_on=True)
        
        assert fast_sm.zone.state == ZoneState.WARNING
        callback.assert_called_once()
    
    def test_alarm_callback(self, fast_sm):
        """测试报警回调触发"""
        from src.zone.models import ZoneState
        
        callback = MagicMock()
        fast_sm.set_callbacks(on_alarm=callback)
        
        # 模拟时间流逝到达报警阈值
        fast_sm._no_person_start_time = time.time() - 2.5
        fast_sm.update(has_person=False, is_fire_on=True)
        
        assert fast_sm.zone.state == ZoneState.ALARM
        callback.assert_called_once()
    
    def test_cutoff_callback(self, fast_sm):
        """测试切电回调触发"""
        from src.zone.models import ZoneState
        
        callback = MagicMock()
        fast_sm.set_callbacks(on_cutoff=callback)
        
        # 模拟时间流逝到达切电阈值
        fast_sm._no_person_start_time = time.time() - 4
        fast_sm.update(has_person=False, is_fire_on=True)
        
        assert fast_sm.zone.state == ZoneState.CUTOFF
        callback.assert_called_once()


class TestManualReset:
    """测试手动复位功能"""
    
    @pytest.fixture
    def setup_sm(self):
        """设置状态机"""
        mock_config = MockAppConfig()
        mock_logger = MagicMock()
        mock_event_logger = MagicMock()
        
        with patch('src.zone.state_machine.get_config', return_value=mock_config), \
             patch('src.zone.state_machine.get_logger', return_value=mock_logger), \
             patch('src.zone.state_machine.event_logger', mock_event_logger):
            from src.zone.state_machine import ZoneStateMachine
            
            config = MockZoneConfig(
                id="zone_reset",
                name="复位灶台",
                camera_id="cam_1",
                roi=[(0, 0)]
            )
            sm = ZoneStateMachine(config)
            yield sm, mock_event_logger
    
    def test_reset_from_warning(self, setup_sm):
        """测试从预警状态复位"""
        from src.zone.models import ZoneState
        
        sm, event_logger = setup_sm
        
        # 手动设置为预警状态
        sm.zone.state = ZoneState.WARNING
        
        result = sm.reset()
        
        assert result is True
        assert sm.zone.state == ZoneState.IDLE
        event_logger.log_reset.assert_called()
    
    def test_reset_from_cutoff(self, setup_sm):
        """测试从切电状态复位"""
        from src.zone.models import ZoneState
        
        sm, event_logger = setup_sm
        
        sm.zone.state = ZoneState.CUTOFF
        result = sm.reset()
        
        assert result is True
        assert sm.zone.state == ZoneState.IDLE
    
    def test_reset_from_idle_fails(self, setup_sm):
        """测试从空闲状态复位失败"""
        from src.zone.models import ZoneState
        
        sm, _ = setup_sm
        
        sm.zone.state = ZoneState.IDLE
        result = sm.reset()
        
        assert result is False
        assert sm.zone.state == ZoneState.IDLE
    
    def test_reset_from_active_fails(self, setup_sm):
        """测试从有人动火状态复位失败"""
        from src.zone.models import ZoneState
        
        sm, _ = setup_sm
        
        sm.zone.state = ZoneState.ACTIVE_WITH_PERSON
        result = sm.reset()
        
        assert result is False


class TestStateChangeEvent:
    """测试状态变化事件"""
    
    @pytest.fixture
    def sm_with_callback(self):
        """创建带状态变化回调的状态机"""
        mock_config = MockAppConfig()
        mock_logger = MagicMock()
        mock_event_logger = MagicMock()
        
        with patch('src.zone.state_machine.get_config', return_value=mock_config), \
             patch('src.zone.state_machine.get_logger', return_value=mock_logger), \
             patch('src.zone.state_machine.event_logger', mock_event_logger):
            from src.zone.state_machine import ZoneStateMachine
            
            config = MockZoneConfig(
                id="zone_event",
                name="事件灶台",
                camera_id="cam_1",
                roi=[]
            )
            sm = ZoneStateMachine(config)
            callback = MagicMock()
            sm.set_callbacks(on_state_change=callback)
            yield sm, callback
    
    def test_state_change_event_fired(self, sm_with_callback):
        """测试状态变化事件被触发"""
        sm, callback = sm_with_callback
        
        # 触发状态变化
        sm.update(has_person=True, is_fire_on=True)
        
        # 验证回调被调用
        callback.assert_called_once()
        
        # 验证事件内容
        event = callback.call_args[0][0]
        assert event.zone_id == "zone_event"
        assert event.zone_name == "事件灶台"
    
    def test_no_event_when_same_state(self, sm_with_callback):
        """测试相同状态不触发事件"""
        sm, callback = sm_with_callback
        
        # 第一次更新触发事件
        sm.update(has_person=True, is_fire_on=True)
        callback.reset_mock()
        
        # 相同状态不应触发事件
        sm.update(has_person=True, is_fire_on=True)
        callback.assert_not_called()
