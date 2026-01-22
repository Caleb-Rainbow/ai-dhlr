"""
GPIO 指示灯控制模块
通过 sysfs 接口控制外部 GPIO，用于驱动动火、离人、报警三个指示灯
"""
import os
from pathlib import Path
from typing import List, Optional
from ..utils.logger import get_logger

logger = get_logger()


class SysfsGpioController:
    """通过 sysfs 接口控制 GPIO"""
    
    def __init__(self, gpio_path: str = "/sys/external_gpio"):
        self.gpio_path = Path(gpio_path)
        self._initialized_pins: set = set()
    
    def is_available(self) -> bool:
        """检查 GPIO sysfs 接口是否可用"""
        return self.gpio_path.exists() and self.gpio_path.is_dir()
    
    def list_available_pins(self) -> List[str]:
        """枚举可用的 GPIO 引脚（返回 gpioX 格式的引脚名称）"""
        if not self.is_available():
            return []
        
        pins = []
        try:
            for item in self.gpio_path.iterdir():
                # 匹配 jwsioc_gpioX 格式，提取 gpioX 部分
                if item.name.startswith("jwsioc_gpio") and not item.name.startswith("jwsioc_inout_"):
                    # jwsioc_gpio0 -> gpio0
                    pin_name = item.name.replace("jwsioc_", "")
                    pins.append(pin_name)
            # 按数字排序
            pins.sort(key=lambda x: int(x.replace("gpio", "")) if x.replace("gpio", "").isdigit() else 999)
        except Exception as e:
            logger.error(f"枚举 GPIO 引脚失败: {e}")
        
        return pins
    
    def _get_gpio_file(self, pin: str) -> Path:
        """获取 GPIO 值文件路径"""
        # gpio0 -> jwsioc_gpio0
        return self.gpio_path / f"jwsioc_{pin}"
    
    def _get_direction_file(self, pin: str) -> Path:
        """获取 GPIO 方向文件路径"""
        # gpio0 -> jwsioc_inout_gpio0
        return self.gpio_path / f"jwsioc_inout_{pin}"
    
    def set_direction(self, pin: str, output: bool = True) -> bool:
        """
        设置引脚方向
        Args:
            pin: 引脚名称，如 "gpio0"
            output: True 为输出模式，False 为输入模式
        """
        direction_file = self._get_direction_file(pin)
        if not direction_file.exists():
            logger.warning(f"GPIO 方向文件不存在: {direction_file}")
            return False
        
        try:
            with open(direction_file, 'w') as f:
                f.write("1" if output else "0")
            self._initialized_pins.add(pin)
            return True
        except Exception as e:
            logger.error(f"设置 GPIO {pin} 方向失败: {e}")
            return False
    
    def write(self, pin: str, value: bool) -> bool:
        """
        设置引脚电平
        Args:
            pin: 引脚名称，如 "gpio0"
            value: True 为高电平，False 为低电平
        """
        # 确保引脚已初始化为输出模式
        if pin not in self._initialized_pins:
            if not self.set_direction(pin, output=True):
                return False
        
        gpio_file = self._get_gpio_file(pin)
        if not gpio_file.exists():
            logger.warning(f"GPIO 文件不存在: {gpio_file}")
            return False
        
        try:
            with open(gpio_file, 'w') as f:
                f.write("1" if value else "0")
            return True
        except Exception as e:
            logger.error(f"设置 GPIO {pin} 电平失败: {e}")
            return False
    
    def read(self, pin: str) -> Optional[bool]:
        """
        读取引脚电平
        Args:
            pin: 引脚名称，如 "gpio0"
        Returns:
            True 为高电平，False 为低电平，None 为读取失败
        """
        gpio_file = self._get_gpio_file(pin)
        if not gpio_file.exists():
            logger.warning(f"GPIO 文件不存在: {gpio_file}")
            return None
        
        try:
            with open(gpio_file, 'r') as f:
                value = f.read().strip()
                return value == "1"
        except Exception as e:
            logger.error(f"读取 GPIO {pin} 电平失败: {e}")
            return None


class IndicatorController:
    """指示灯控制器"""
    
    def __init__(self, gpio_config):
        """
        初始化指示灯控制器
        Args:
            gpio_config: GpioConfig 配置对象
        """
        self.config = gpio_config
        self._gpio = SysfsGpioController(gpio_config.gpio_path)
        self._last_states = {"fire": None, "absence": None, "alarm": None}
        
        # 初始化 GPIO 引脚
        if self.config.enabled and self._gpio.is_available():
            self._initialize_pins()
    
    def _initialize_pins(self):
        """初始化所有引脚为输出模式"""
        pins = [
            self.config.pin_fire,
            self.config.pin_absence,
            self.config.pin_alarm
        ]
        for pin in pins:
            if pin:
                self._gpio.set_direction(pin, output=True)
                self._gpio.write(pin, True)  # 初始化为高电平（灭灯，低电平为亮）
        logger.info(f"GPIO 指示灯初始化完成 (动火: {self.config.pin_fire}, 离人: {self.config.pin_absence}, 报警: {self.config.pin_alarm})")
    
    def is_available(self) -> bool:
        """检查指示灯控制器是否可用"""
        return self.config.enabled and self._gpio.is_available()
    
    def update_indicators(self, fire_on: bool, absence: bool, alarm: bool):
        """
        根据系统状态更新指示灯
        
        Args:
            fire_on: 任意区域是否处于动火状态
            absence: 任意区域是否处于离人状态（无人且动火中）
            alarm: 任意区域是否处于报警状态（预警/报警/切电）
        """
        if not self.config.enabled:
            return
        
        if not self._gpio.is_available():
            return
        
        # 只有状态变化时才更新 GPIO（低电平为亮，高电平为灭，所以取反）
        if fire_on != self._last_states["fire"]:
            self._gpio.write(self.config.pin_fire, not fire_on)  # 取反：亮时写低电平，灭时写高电平
            self._last_states["fire"] = fire_on
            logger.info(f"动火指示灯: {'亮' if fire_on else '灭'}")
        
        if absence != self._last_states["absence"]:
            self._gpio.write(self.config.pin_absence, not absence)  # 取反：亮时写低电平，灭时写高电平
            self._last_states["absence"] = absence
            logger.info(f"离人指示灯: {'亮' if absence else '灭'}")
        
        if alarm != self._last_states["alarm"]:
            self._gpio.write(self.config.pin_alarm, not alarm)  # 取反：亮时写低电平，灭时写高电平
            self._last_states["alarm"] = alarm
            logger.info(f"报警指示灯: {'亮' if alarm else '灭'}")
    
    def turn_off_all(self):
        """关闭所有指示灯"""
        if not self.config.enabled:
            return
        
        if not self._gpio.is_available():
            return
        
        self._gpio.write(self.config.pin_fire, True)  # 高电平为灭
        self._gpio.write(self.config.pin_absence, True)  # 高电平为灭
        self._gpio.write(self.config.pin_alarm, True)  # 高电平为灭
        self._last_states = {"fire": False, "absence": False, "alarm": False}
        logger.info("所有指示灯已关闭")
    
    def reload_config(self, gpio_config):
        """重新加载配置"""
        self.config = gpio_config
        self._gpio = SysfsGpioController(gpio_config.gpio_path)
        self._last_states = {"fire": None, "absence": None, "alarm": None}
        
        if self.config.enabled and self._gpio.is_available():
            self._initialize_pins()


# 全局指示灯控制器实例
_indicator_controller: Optional[IndicatorController] = None


def get_indicator_controller() -> Optional[IndicatorController]:
    """获取全局指示灯控制器"""
    return _indicator_controller


def init_indicator_controller(gpio_config) -> IndicatorController:
    """初始化全局指示灯控制器"""
    global _indicator_controller
    _indicator_controller = IndicatorController(gpio_config)
    return _indicator_controller


def list_gpio_pins(gpio_path: str = "/sys/external_gpio") -> List[str]:
    """枚举可用的 GPIO 引脚"""
    gpio = SysfsGpioController(gpio_path)
    return gpio.list_available_pins()
