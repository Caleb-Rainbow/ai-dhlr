"""
测试 zone/models.py 模块

测试内容:
- ZoneState 枚举值完整性
- Zone.to_dict() 序列化
- Zone.get_status_text() 状态文本映射
"""
import pytest

from src.zone.models import Zone, ZoneState


class TestZoneState:
    """测试 ZoneState 枚举"""
    
    def test_all_states_exist(self):
        """验证所有期望的状态值都存在"""
        expected_states = [
            ("IDLE", "idle"),
            ("ACTIVE_WITH_PERSON", "active_with_person"),
            ("ACTIVE_NO_PERSON", "active_no_person"),
            ("WARNING", "warning"),
            ("ALARM", "alarm"),
            ("CUTOFF", "cutoff"),
        ]
        
        for attr_name, value in expected_states:
            assert hasattr(ZoneState, attr_name), f"ZoneState 缺少 {attr_name}"
            assert getattr(ZoneState, attr_name).value == value
    
    def test_state_count(self):
        """验证状态数量"""
        assert len(ZoneState) == 6, "ZoneState 应该有6个状态"


class TestZone:
    """测试 Zone 数据类"""
    
    @pytest.fixture
    def sample_zone(self):
        """创建测试用 Zone 实例"""
        return Zone(
            id="zone_test",
            name="测试灶台",
            camera_id="cam_1",
            roi=[(0.1, 0.2), (0.8, 0.2), (0.8, 0.9), (0.1, 0.9)],
            enabled=True,
            state=ZoneState.IDLE,
            is_fire_on=False,
            has_person=False,
        )
    
    def test_zone_creation(self, sample_zone):
        """测试 Zone 创建"""
        assert sample_zone.id == "zone_test"
        assert sample_zone.name == "测试灶台"
        assert sample_zone.camera_id == "cam_1"
        assert sample_zone.enabled is True
        assert sample_zone.state == ZoneState.IDLE
    
    def test_zone_default_values(self):
        """测试 Zone 默认值"""
        zone = Zone(
            id="z1",
            name="灶台1",
            camera_id="c1",
            roi=[(0, 0), (1, 0), (1, 1), (0, 1)]
        )
        assert zone.enabled is True
        assert zone.state == ZoneState.IDLE
        assert zone.is_fire_on is False
        assert zone.has_person is False
        assert zone.no_person_duration == 0.0
        assert zone.warning_remaining == 0.0
        assert zone.alarm_remaining == 0.0
        assert zone.cutoff_remaining == 0.0
        assert zone.last_snapshot_path is None
    
    def test_to_dict(self, sample_zone):
        """测试 to_dict() 序列化"""
        result = sample_zone.to_dict()
        
        assert isinstance(result, dict)
        assert result["id"] == "zone_test"
        assert result["name"] == "测试灶台"
        assert result["camera_id"] == "cam_1"
        assert result["enabled"] is True
        assert result["state"] == "idle"  # 枚举值
        assert result["is_fire_on"] is False
        assert result["has_person"] is False
        # ROI 应该是列表的列表
        assert isinstance(result["roi"], list)
        assert len(result["roi"]) == 4
    
    def test_to_dict_with_countdown(self):
        """测试带倒计时的 to_dict()"""
        zone = Zone(
            id="z1",
            name="灶台",
            camera_id="c1",
            roi=[(0, 0)],
            state=ZoneState.WARNING,
            no_person_duration=95.5,
            warning_remaining=0.0,
            alarm_remaining=84.5,
            cutoff_remaining=204.5
        )
        result = zone.to_dict()
        
        assert result["state"] == "warning"
        assert result["no_person_duration"] == 95.5
        assert result["warning_remaining"] == 0.0
        assert result["alarm_remaining"] == 84.5
        assert result["cutoff_remaining"] == 204.5


class TestZoneStatusText:
    """测试 Zone.get_status_text() 方法"""
    
    @pytest.mark.parametrize("state, expected_text", [
        (ZoneState.IDLE, "空闲"),
        (ZoneState.ACTIVE_WITH_PERSON, "有人看管"),
        (ZoneState.ACTIVE_NO_PERSON, "无人看管"),
        (ZoneState.WARNING, "预警中"),
        (ZoneState.ALARM, "报警中"),
        (ZoneState.CUTOFF, "已切电"),
    ])
    def test_status_text_mapping(self, state, expected_text):
        """测试各状态的文本映射"""
        zone = Zone(
            id="z1",
            name="测试",
            camera_id="c1",
            roi=[],
            state=state
        )
        assert zone.get_status_text() == expected_text
