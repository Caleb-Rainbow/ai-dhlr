"""
灶台/区域数据模型
"""
from dataclasses import dataclass, field
from typing import List, Tuple, Optional
from enum import Enum


class ZoneState(Enum):
    """灶台状态枚举"""
    IDLE = "idle"                      # 空闲（未开火）
    ACTIVE_WITH_PERSON = "active_with_person"  # 有人动火
    ACTIVE_NO_PERSON = "active_no_person"      # 无人动火（计时中）
    WARNING = "warning"                # 预警中
    ALARM = "alarm"                    # 报警中
    CUTOFF = "cutoff"                  # 已切电
    TEMP_ALARM = "temp_alarm"          # 温度报警中


@dataclass
class Zone:
    """灶台/区域"""
    id: str
    name: str
    camera_id: str
    roi: List[Tuple[float, float]]
    enabled: bool = True
    
    # 运行时状态
    state: ZoneState = ZoneState.IDLE
    is_fire_on: bool = False           # 是否开火
    has_person: bool = False           # 是否有人
    no_person_duration: float = 0.0    # 无人持续时间（秒）
    warning_remaining: float = 0.0     # 到预警倒计时剩余（秒）
    alarm_remaining: float = 0.0       # 到报警倒计时剩余（秒）
    cutoff_remaining: float = 0.0      # 到切电倒计时剩余（秒）
    current_value: float = 0.0         # 实时电流值（安培 x 100，或原始值）
    temperature: float = 0.0           # 实时温度值 (°C)
    last_snapshot_path: Optional[str] = None  # 最后截图路径
    
    def to_dict(self) -> dict:
        """转换为字典（用于API响应）"""
        return {
            "id": self.id,
            "name": self.name,
            "camera_id": self.camera_id,
            "roi": [list(p) for p in self.roi],
            "enabled": self.enabled,
            "state": self.state.value,
            "is_fire_on": self.is_fire_on,
            "has_person": self.has_person,
            "no_person_duration": round(self.no_person_duration, 1),
            "warning_remaining": round(self.warning_remaining, 1),
            "alarm_remaining": round(self.alarm_remaining, 1),
            "cutoff_remaining": round(self.cutoff_remaining, 1),
            "current_value": self.current_value,
            "temperature": round(self.temperature, 1),
            "last_snapshot_path": self.last_snapshot_path
        }
    
    def get_status_text(self) -> str:
        """获取状态文本描述"""
        status_texts = {
            ZoneState.IDLE: "空闲",
            ZoneState.ACTIVE_WITH_PERSON: "有人看管",
            ZoneState.ACTIVE_NO_PERSON: "无人看管",
            ZoneState.WARNING: "预警中",
            ZoneState.ALARM: "报警中",
            ZoneState.CUTOFF: "已切电",
            ZoneState.TEMP_ALARM: "温度报警"
        }
        return status_texts.get(self.state, "未知")
