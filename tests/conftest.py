"""
测试配置和共享 fixtures
"""
import pytest
from unittest.mock import MagicMock, patch
from dataclasses import dataclass
from typing import List, Tuple


# ==================== Mock 配置数据类 ====================

@dataclass
class MockAlarmConfig:
    """模拟报警配置"""
    warning_time: int = 90
    alarm_time: int = 180
    action_time: int = 300
    broadcast_interval: int = 15
    warning_message: str = "预警测试消息"
    alarm_message: str = "报警测试消息"
    action_message: str = "切电测试消息"


@dataclass
class MockAppConfig:
    """模拟应用配置"""
    alarm: MockAlarmConfig = None
    
    def __post_init__(self):
        if self.alarm is None:
            self.alarm = MockAlarmConfig()


# ==================== Fixtures ====================

@pytest.fixture
def mock_config():
    """提供测试用配置"""
    return MockAppConfig()


@pytest.fixture
def mock_logger():
    """提供 mock logger"""
    logger = MagicMock()
    logger.info = MagicMock()
    logger.debug = MagicMock()
    logger.warning = MagicMock()
    logger.error = MagicMock()
    return logger


@pytest.fixture
def mock_event_logger():
    """提供 mock event_logger"""
    event_logger = MagicMock()
    event_logger.log_warning = MagicMock()
    event_logger.log_cutoff = MagicMock()
    event_logger.log_reset = MagicMock()
    event_logger.save_snapshot = MagicMock(return_value="/mock/snapshot.jpg")
    return event_logger


@pytest.fixture
def sample_zone_config():
    """提供测试用灶台配置"""
    # 创建一个简单的配置对象用于测试
    @dataclass
    class ZoneConfig:
        id: str
        name: str
        camera_id: str
        roi: List[Tuple[float, float]]
        enabled: bool = True
        serial_index: int = 0
        fire_current_threshold: int = 100
    
    return ZoneConfig(
        id="zone_1",
        name="测试灶台",
        camera_id="cam_1",
        roi=[(0.1, 0.1), (0.9, 0.1), (0.9, 0.9), (0.1, 0.9)],
        enabled=True
    )


@pytest.fixture
def patched_dependencies(mock_config, mock_logger, mock_event_logger):
    """
    统一 patch 所有外部依赖
    """
    with patch('src.zone.state_machine.get_config', return_value=mock_config), \
         patch('src.zone.state_machine.get_logger', return_value=mock_logger), \
         patch('src.zone.state_machine.event_logger', mock_event_logger):
        yield {
            'config': mock_config,
            'logger': mock_logger,
            'event_logger': mock_event_logger
        }
