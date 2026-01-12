"""
巡检管理器单元测试
测试 patrol_manager.py 中的巡检功能
"""
import pytest
from unittest.mock import MagicMock, patch, PropertyMock
from dataclasses import dataclass


# ==================== Mock 类 ====================

@dataclass
class MockZone:
    """模拟灶台对象"""
    id: str
    name: str
    has_person: bool = False


class MockStateMachine:
    """模拟状态机"""
    def __init__(self, zone_id: str, zone_name: str, has_person: bool = False):
        self.zone = MockZone(id=zone_id, name=zone_name, has_person=has_person)


class MockZoneManager:
    """模拟灶台管理器"""
    def __init__(self):
        self._zones = {}
    
    def add_zone(self, zone_id: str, zone_name: str, has_person: bool = False):
        self._zones[zone_id] = MockStateMachine(zone_id, zone_name, has_person)
    
    def get_zone(self, zone_id: str):
        return self._zones.get(zone_id)
    
    def get_all_zones(self):
        return list(self._zones.values())


class MockSerialManager:
    """模拟串口管理器"""
    def __init__(self):
        self._fire_states = {}
    
    def set_fire_state(self, zone_id: str, is_on: bool):
        self._fire_states[zone_id] = is_on
    
    def is_fire_on(self, zone_id: str) -> bool:
        return self._fire_states.get(zone_id, False)
    
    def cutoff(self, zone_id: str) -> bool:
        return True


class MockTTSManager:
    """模拟TTS管理器"""
    def get_audio_path(self, zone_id: str, audio_type) -> str:
        return f"/mock/audio/{zone_id}/{audio_type.value}.wav"


class MockVoicePlayer:
    """模拟语音播放器"""
    def __init__(self):
        self.played_files = []
    
    def play_file(self, path: str, priority: bool = False):
        self.played_files.append(path)
    
    def stop_playback(self):
        pass


# ==================== Fixtures ====================

@pytest.fixture
def mock_zone_manager():
    """提供模拟灶台管理器"""
    mgr = MockZoneManager()
    mgr.add_zone("zone_1", "1号灶台", has_person=False)
    mgr.add_zone("zone_2", "2号灶台", has_person=True)
    return mgr


@pytest.fixture
def mock_serial_manager():
    """提供模拟串口管理器"""
    mgr = MockSerialManager()
    mgr.set_fire_state("zone_1", True)
    mgr.set_fire_state("zone_2", False)
    return mgr


@pytest.fixture
def mock_tts_manager():
    """提供模拟TTS管理器"""
    return MockTTSManager()


@pytest.fixture
def mock_voice_player():
    """提供模拟语音播放器"""
    return MockVoicePlayer()


@pytest.fixture
def patrol_manager_with_mocks(mock_zone_manager, mock_serial_manager, mock_tts_manager, mock_voice_player):
    """创建带有模拟依赖的巡检管理器"""
    with patch('src.patrol.patrol_manager.get_logger') as mock_logger, \
         patch('src.patrol.patrol_manager.tts_manager', mock_tts_manager), \
         patch('src.patrol.patrol_manager.voice_player', mock_voice_player):
        
        mock_logger.return_value = MagicMock()
        
        # 重置单例
        from src.patrol.patrol_manager import PatrolManager
        PatrolManager._instance = None
        
        patrol_mgr = PatrolManager()
        
        yield {
            'patrol_manager': patrol_mgr,
            'zone_manager': mock_zone_manager,
            'serial_manager': mock_serial_manager,
            'tts_manager': mock_tts_manager,
            'voice_player': mock_voice_player
        }


# ==================== 测试用例 ====================

class TestPatrolManagerBasic:
    """巡检管理器基础测试"""
    
    def test_start_patrol(self, patrol_manager_with_mocks):
        """测试开始巡检"""
        pm = patrol_manager_with_mocks['patrol_manager']
        
        result = pm.start_patrol()
        
        assert result['success'] is True
        assert pm.is_active is True
    
    def test_start_patrol_already_active(self, patrol_manager_with_mocks):
        """测试重复开始巡检"""
        pm = patrol_manager_with_mocks['patrol_manager']
        
        pm.start_patrol()
        result = pm.start_patrol()
        
        assert result['success'] is False
        assert "已在进行中" in result['message']
    
    def test_stop_patrol(self, patrol_manager_with_mocks):
        """测试退出巡检"""
        pm = patrol_manager_with_mocks['patrol_manager']
        
        pm.start_patrol()
        result = pm.stop_patrol()
        
        assert result['success'] is True
        assert pm.is_active is False
    
    def test_get_state(self, patrol_manager_with_mocks):
        """测试获取巡检状态"""
        pm = patrol_manager_with_mocks['patrol_manager']
        
        pm.start_patrol()
        state = pm.get_state()
        
        assert state['is_active'] is True
        assert state['current_step'] == 'idle'
        assert state['progress'] == 0


class TestCheckPersonZone:
    """离人检测测试"""
    
    def test_check_person_zone_not_active(self, patrol_manager_with_mocks):
        """测试未开启巡检时检测离人"""
        pm = patrol_manager_with_mocks['patrol_manager']
        
        result = pm.check_person_zone("zone_1")
        
        assert result['success'] is False
        assert "开启巡检" in result['message']
    
    def test_check_person_zone_not_found(self, patrol_manager_with_mocks):
        """测试检测不存在的灶台"""
        pm = patrol_manager_with_mocks['patrol_manager']
        zm = patrol_manager_with_mocks['zone_manager']
        
        pm.start_patrol()
        
        with patch('src.zone.state_machine.zone_manager', zm):
            result = pm.check_person_zone("invalid_zone")
        
        assert result['success'] is False
        assert "不存在" in result['message']
    
    def test_check_person_zone_no_person(self, patrol_manager_with_mocks):
        """测试检测无人灶台"""
        pm = patrol_manager_with_mocks['patrol_manager']
        zm = patrol_manager_with_mocks['zone_manager']
        vp = patrol_manager_with_mocks['voice_player']
        
        pm.start_patrol()
        
        with patch('src.zone.state_machine.zone_manager', zm):
            result = pm.check_person_zone("zone_1")
        
        assert result['success'] is True
        assert result['has_person'] is False
        assert "没人" in result['message']
        assert len(vp.played_files) > 0
    
    def test_check_person_zone_has_person(self, patrol_manager_with_mocks):
        """测试检测有人灶台"""
        pm = patrol_manager_with_mocks['patrol_manager']
        zm = patrol_manager_with_mocks['zone_manager']
        vp = patrol_manager_with_mocks['voice_player']
        
        pm.start_patrol()
        
        with patch('src.zone.state_machine.zone_manager', zm):
            result = pm.check_person_zone("zone_2")
        
        assert result['success'] is True
        assert result['has_person'] is True
        assert "有人" in result['message']


class TestCheckFireZone:
    """动火检测测试"""
    
    def test_check_fire_zone_not_active(self, patrol_manager_with_mocks):
        """测试未开启巡检时检测动火"""
        pm = patrol_manager_with_mocks['patrol_manager']
        
        result = pm.check_fire_zone("zone_1")
        
        assert result['success'] is False
    
    def test_check_fire_zone_fire_on(self, patrol_manager_with_mocks):
        """测试检测动火中的灶台"""
        pm = patrol_manager_with_mocks['patrol_manager']
        zm = patrol_manager_with_mocks['zone_manager']
        sm = patrol_manager_with_mocks['serial_manager']
        
        pm.start_patrol()
        
        with patch('src.zone.state_machine.zone_manager', zm), \
             patch('src.serial_port.serial_manager.serial_manager', sm):
            result = pm.check_fire_zone("zone_1")
        
        assert result['success'] is True
        assert result['is_fire_on'] is True
        assert "动火" in result['message']
    
    def test_check_fire_zone_no_fire(self, patrol_manager_with_mocks):
        """测试检测未动火的灶台"""
        pm = patrol_manager_with_mocks['patrol_manager']
        zm = patrol_manager_with_mocks['zone_manager']
        sm = patrol_manager_with_mocks['serial_manager']
        
        pm.start_patrol()
        
        with patch('src.zone.state_machine.zone_manager', zm), \
             patch('src.serial_port.serial_manager.serial_manager', sm):
            result = pm.check_fire_zone("zone_2")
        
        assert result['success'] is True
        assert result['is_fire_on'] is False
        assert "未动火" in result['message']


class TestAlarmDemoZone:
    """报警演示测试"""
    
    def test_alarm_demo_zone_not_active(self, patrol_manager_with_mocks):
        """测试未开启巡检时报警演示"""
        pm = patrol_manager_with_mocks['patrol_manager']
        
        result = pm.alarm_demo_zone("zone_1")
        
        assert result['success'] is False
    
    def test_alarm_demo_zone_no_fire(self, patrol_manager_with_mocks):
        """测试未动火时报警演示"""
        pm = patrol_manager_with_mocks['patrol_manager']
        zm = patrol_manager_with_mocks['zone_manager']
        sm = patrol_manager_with_mocks['serial_manager']
        
        pm.start_patrol()
        
        with patch('src.zone.state_machine.zone_manager', zm), \
             patch('src.serial_port.serial_manager.serial_manager', sm):
            result = pm.alarm_demo_zone("zone_2")  # zone_2 未动火
        
        assert result['success'] is False
        assert "未动火" in result['message']
    
    def test_alarm_demo_zone_fire_on(self, patrol_manager_with_mocks):
        """测试动火时报警演示启动"""
        pm = patrol_manager_with_mocks['patrol_manager']
        zm = patrol_manager_with_mocks['zone_manager']
        sm = patrol_manager_with_mocks['serial_manager']
        
        pm.start_patrol()
        
        with patch('src.zone.state_machine.zone_manager', zm), \
             patch('src.serial_port.serial_manager.serial_manager', sm):
            result = pm.alarm_demo_zone("zone_1")  # zone_1 动火中
        
        assert result['success'] is True
        assert "已启动" in result['message']


class TestCutoffZone:
    """切电测试"""
    
    def test_cutoff_zone_not_active(self, patrol_manager_with_mocks):
        """测试未开启巡检时切电"""
        pm = patrol_manager_with_mocks['patrol_manager']
        
        result = pm.cutoff_zone("zone_1")
        
        assert result['success'] is False
    
    def test_cutoff_zone_success(self, patrol_manager_with_mocks):
        """测试成功切电"""
        pm = patrol_manager_with_mocks['patrol_manager']
        zm = patrol_manager_with_mocks['zone_manager']
        sm = patrol_manager_with_mocks['serial_manager']
        
        pm.start_patrol()
        
        with patch('src.zone.state_machine.zone_manager', zm), \
             patch('src.serial_port.serial_manager.serial_manager', sm):
            result = pm.cutoff_zone("zone_1")
        
        assert result['success'] is True
        assert "已切电" in result['message']

