"""
测试 src/output/gpio.py 的 EStopMonitor 急停监听器

测试内容：
- 去抖确认（连续 N 次按下电平才触发）
- 下降沿只触发一次（持续按住不重复触发）
- 释放后才能再次触发（重新武装）
- cooldown 冷却期内重复按下被忽略
- active_low / active_high 极性正确
- raw=None 读取失败时状态不变、不触发
- is_available() 降级判断（未启用/无引脚/sysfs 不可用）
- 回调抛异常不会向上传播
"""
import time
from types import SimpleNamespace

from src.output.gpio import EStopMonitor


def _cfg(active_low=True, pin="gpio10", enabled=True, gpio_path="/sys/external_gpio"):
    """构造一个最小可用的 GpioConfig 替身"""
    return SimpleNamespace(
        gpio_path=gpio_path,
        pin_estop=pin,
        estop_enabled=enabled,
        estop_active_low=active_low,
    )


class TestEStopMonitor:
    def test_debounce_and_hold(self):
        calls = []
        mon = EStopMonitor(_cfg(), on_trigger=lambda: calls.append(1),
                           debounce_count=3, cooldown_s=1000)
        # 低电平有效：按下 = 读到 False；前 2 次不触发，第 3 次触发
        mon._process_sample(False)
        mon._process_sample(False)
        assert len(calls) == 0
        mon._process_sample(False)
        assert len(calls) == 1
        # 持续按住不再触发（下降沿锁定）
        for _ in range(5):
            mon._process_sample(False)
        assert len(calls) == 1

    def test_release_rearms(self):
        calls = []
        mon = EStopMonitor(_cfg(), on_trigger=lambda: calls.append(1),
                           debounce_count=3, cooldown_s=1000)
        for _ in range(3):
            mon._process_sample(False)
        assert len(calls) == 1
        # 模拟首次触发的 cooldown 已过，释放（读到高电平 True）后再次按下应再次触发
        mon._last_trigger_ts = time.time() - 1001
        mon._process_sample(True)
        for _ in range(3):
            mon._process_sample(False)
        assert len(calls) == 2

    def test_active_high_polarity(self):
        calls = []
        mon = EStopMonitor(_cfg(active_low=False), on_trigger=lambda: calls.append(1),
                           debounce_count=2, cooldown_s=1000)
        # 高电平有效：按下 = 读到 True；读到 False(低) 不算按下
        mon._process_sample(False)
        mon._process_sample(False)
        assert len(calls) == 0
        mon._process_sample(True)
        mon._process_sample(True)
        assert len(calls) == 1

    def test_cooldown_blocks_repeat(self):
        calls = []
        mon = EStopMonitor(_cfg(), on_trigger=lambda: calls.append(1),
                           debounce_count=2, cooldown_s=1000)
        # 首次触发
        mon._process_sample(False)
        mon._process_sample(False)
        assert len(calls) == 1
        # 释放后立即再按 —— cooldown 内不应触发
        mon._process_sample(True)
        mon._process_sample(False)
        mon._process_sample(False)
        assert len(calls) == 1
        # 把上次触发时间往前拨到 cooldown 之外，释放后再按应触发
        mon._last_trigger_ts = time.time() - 1001
        mon._process_sample(True)
        mon._process_sample(False)
        mon._process_sample(False)
        assert len(calls) == 2

    def test_none_sample_no_change(self):
        calls = []
        mon = EStopMonitor(_cfg(), on_trigger=lambda: calls.append(1),
                           debounce_count=2, cooldown_s=1000)
        mon._process_sample(False)
        assert mon._consecutive == 1
        # 读取失败(raw=None)不应改变状态，也不应触发
        mon._process_sample(None)
        assert mon._consecutive == 1
        mon._process_sample(None)
        assert len(calls) == 0

    def test_is_available_disabled(self):
        mon = EStopMonitor(_cfg(enabled=False), on_trigger=lambda: None)
        assert mon.is_available() is False

    def test_is_available_no_pin(self):
        mon = EStopMonitor(_cfg(pin=""), on_trigger=lambda: None)
        assert mon.is_available() is False

    def test_is_available_sysfs_missing(self, tmp_path):
        cfg = _cfg(gpio_path=str(tmp_path / "no_such_gpio"))
        mon = EStopMonitor(cfg, on_trigger=lambda: None)
        assert mon.is_available() is False

    def test_callback_exception_does_not_propagate(self):
        def bad():
            raise RuntimeError("boom")
        mon = EStopMonitor(_cfg(), on_trigger=bad, debounce_count=2, cooldown_s=1000)
        mon._process_sample(False)
        triggered = mon._process_sample(False)  # 达到去抖，回调抛异常应被吞掉
        assert triggered is True
