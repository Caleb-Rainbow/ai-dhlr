"""开机自启热点开关（root，经 sudo systemctl）。

设备上 ``hotspot-startup.service`` 在开机时执行
``/usr/local/bin/auto_hotspot.sh``，用 nmcli 起 WiFi 热点（``dhlr_XXXX``）。
本模块按配置启用 / 禁用该 systemd 单元：

- ``get_state()``            读 ``is-enabled`` + wlan0 是否正处 AP（热点在跑）。
- ``enable()``               ``systemctl enable``（仅下次开机生效，不动当前网络，
                              避免把设备从现有 WiFi 踢下线）。
- ``disable()``              ``systemctl disable --now`` + 立即关掉 wlan0 上活动 AP 连接。
- ``apply_autostart(bool)``  按布尔值二选一。
- ``reconcile_with_config()`` 启动时按配置对齐 systemd 状态（防御式，失败仅记日志）。

sudo 经可注入 runner 执行，便于单测（命令构造 / 输出解析为纯逻辑）。
非 Linux / 无 sudo / 单元缺失时 ``supported=False``，静默跳过不抛。
"""
from __future__ import annotations

import logging
import sys
from typing import Callable, List, Optional, Tuple

logger = logging.getLogger(__name__)

HOTSPOT_UNIT = "hotspot-startup.service"
SUDO_PASSWORD = "linaro"
DEFAULT_IFACE = "wlan0"

# 子进程执行器：(cmd_with_sudo) -> (returncode, combined_output_text)
Runner = Callable[[List[str]], Tuple[int, str]]


def _default_runner(cmd: List[str]) -> Tuple[int, str]:
    import subprocess

    proc = subprocess.run(
        cmd,
        input=SUDO_PASSWORD.encode(),
        capture_output=True,
        timeout=15,
    )
    out = proc.stdout.decode("utf-8", errors="replace") + \
        proc.stderr.decode("utf-8", errors="replace")
    return proc.returncode, out


def _sudo(cmd: List[str]) -> List[str]:
    """给命令加 sudo -S 前缀（密码经 runner 的 stdin 喂入）。"""
    return ["sudo", "-S", *cmd]


# ---------------------------- 命令构造（纯逻辑） ---------------------------- #
def is_enabled_cmd(unit: str = HOTSPOT_UNIT) -> List[str]:
    return _sudo(["systemctl", "is-enabled", unit])


def enable_cmd(unit: str = HOTSPOT_UNIT) -> List[str]:
    return _sudo(["systemctl", "enable", unit])


def disable_cmd(unit: str = HOTSPOT_UNIT) -> List[str]:
    return _sudo(["systemctl", "disable", "--now", unit])


def parse_is_enabled(out: str, rc: int) -> str:
    """解析 ``systemctl is-enabled`` 输出。

    返回 ``'enabled' | 'disabled' | 'not-found' | 'unknown'``。
    - rc==0：首行即状态，enabled/static/indirect/generated 视为"会被拉起"→ enabled；
      masked 视为永久关闭 → disabled。
    - rc!=0：常见为 disabled（rc=1 输出 disabled）或单元不存在（No such file）。
    """
    text = (out or "").strip()
    first = text.splitlines()[0].strip().lower() if text else ""
    low = text.lower()

    if "no such file" in low or "not-found" in low or "failed to get" in low:
        return "not-found"

    if rc == 0:
        if first in ("enabled", "enabled-runtime", "static", "indirect", "generated"):
            return "enabled"
        if first in ("disabled", "masked", "transient"):
            return "disabled"
        return "enabled" if "enabled" in first else "unknown"

    # rc != 0
    if first == "disabled" or "disabled" in low:
        return "disabled"
    if "masked" in low:
        return "disabled"
    return "not-found"


def _parse_active_ap_lines(active_lines: List[str], mode_lines: List[str], iface: str) -> Optional[str]:
    """从两步 nmcli 输出取 wlan0 上活动 AP 连接名。

    active_lines: ``nmcli -t -f NAME,DEVICE connection show --active``
    mode_lines:   ``nmcli -g 802-11-wireless.mode connection show <conn>``
    两步法避免 -f ...MODE 在 connection show 下报"无效字段 MODE"（见 network_applier 注释）。
    """
    conn: Optional[str] = None
    for line in active_lines:
        name, dev = (line.split(":") + ["", ""])[:2]
        if dev == iface:
            conn = name
            break
    if not conn:
        return None
    mode = next((ln.strip().lower() for ln in mode_lines if ln.strip()), "")
    return conn if mode in ("ap", "hotspot") else None


class HotspotAutostart:
    """开机自启热点开关。"""

    def __init__(
        self,
        runner: Runner = _default_runner,
        iface: str = DEFAULT_IFACE,
        unit: str = HOTSPOT_UNIT,
    ) -> None:
        self._run = runner
        self.iface = iface
        self.unit = unit

    @staticmethod
    def _linux_only() -> bool:
        return sys.platform == "linux"

    # ---------------------------- 读取 ---------------------------- #
    def active_ap_connection(self) -> Optional[str]:
        """wlan0 上活动的 AP（热点）连接名；无或非 AP 模式返回 None。失败返回 None。"""
        try:
            rc1, out1 = self._run(_sudo([
                "nmcli", "-t", "-f", "NAME,DEVICE", "connection", "show", "--active"
            ]))
            if rc1 != 0:
                return None
            conn: Optional[str] = None
            for line in out1.splitlines():
                name, dev = (line.split(":") + ["", ""])[:2]
                if dev == self.iface:
                    conn = name
                    break
            if not conn:
                return None
            rc2, out2 = self._run(_sudo([
                "nmcli", "-g", "802-11-wireless.mode", "connection", "show", conn
            ]))
            mode = next((ln.strip().lower() for ln in out2.splitlines() if ln.strip()), "")
            return conn if mode in ("ap", "hotspot") else None
        except Exception as e:
            logger.debug("读取活动热点连接失败: %s", e)
            return None

    def get_state(self) -> dict:
        """``{supported, enabled, active}``。

        - supported: 设备是否在 Linux 且存在该 systemd 单元。
        - enabled:   is-enabled 是否为 enabled（不支持时为 False）。
        - active:    wlan0 当前是否正处 AP（热点在跑）。
        """
        if not self._linux_only():
            return {"supported": False, "enabled": False, "active": False}
        rc, out = self._run(is_enabled_cmd(self.unit))
        state = parse_is_enabled(out, rc)
        if state == "not-found":
            return {"supported": False, "enabled": False, "active": False}
        return {
            "supported": True,
            "enabled": state == "enabled",
            "active": self.active_ap_connection() is not None,
        }

    # ---------------------------- 写入 ---------------------------- #
    def enable(self) -> dict:
        """``systemctl enable``：仅下次开机生效，不动当前网络。"""
        if not self._linux_only():
            return {"supported": False, "ok": False, "error": "非 Linux 环境，不支持热点自启控制"}
        rc, out = self._run(enable_cmd(self.unit))
        if rc != 0:
            low = out.lower()
            if "no such file" in low or "not-found" in low or "does not exist" in low:
                return {"supported": False, "ok": False, "error": "设备未安装 hotspot-startup.service"}
            return {"supported": True, "ok": False, "error": out.strip()[:200]}
        return {
            "supported": True,
            "ok": True,
            "enabled": True,
            "message": "已启用开机自启热点（下次开机生效）",
        }

    def disable(self) -> dict:
        """``systemctl disable --now`` + 立即关掉 wlan0 上活动 AP 连接。"""
        if not self._linux_only():
            return {"supported": False, "ok": False, "error": "非 Linux 环境，不支持热点自启控制"}
        rc, out = self._run(disable_cmd(self.unit))
        if rc != 0:
            low = out.lower()
            if "no such file" in low or "not-found" in low or "does not exist" in low:
                return {"supported": False, "ok": False, "error": "设备未安装 hotspot-startup.service"}
            # disable 已禁用的单元属正常，不算硬失败
            if "disabled" not in low and "removed" not in low:
                return {"supported": True, "ok": False, "error": out.strip()[:200]}
        # 立即关掉当前活动热点（若有）
        ap = self.active_ap_connection()
        stopped = False
        if ap:
            rc2, _ = self._run(_sudo(["nmcli", "connection", "down", ap]))
            stopped = rc2 == 0
        return {
            "supported": True,
            "ok": True,
            "enabled": False,
            "stopped_hotspot": stopped,
            "message": "已关闭开机自启热点" + ("，当前热点已停止" if stopped else ""),
        }

    def apply_autostart(self, enabled: bool) -> dict:
        """按布尔值二选一。"""
        return self.enable() if enabled else self.disable()


# ---------------------------- 模块级便捷函数 ---------------------------- #
def get_state(runner: Runner = _default_runner) -> dict:
    return HotspotAutostart(runner=runner).get_state()


def apply_autostart(enabled: bool, runner: Runner = _default_runner) -> dict:
    return HotspotAutostart(runner=runner).apply_autostart(enabled)


def reconcile_with_config(enabled: bool, runner: Runner = _default_runner) -> None:
    """启动时按配置对齐 systemd 状态。防御式：任何异常仅记日志，不抛。

    供 main.py 启动时调用，确保手改 config.yaml 也能在下一次启动生效。
    """
    try:
        ha = HotspotAutostart(runner=runner)
        state = ha.get_state()
        if not state.get("supported"):
            logger.info("热点自启同步跳过：设备不支持（非 Linux 或无 hotspot-startup.service）")
            return
        if state["enabled"] != bool(enabled):
            result = ha.apply_autostart(bool(enabled))
            if result.get("ok"):
                logger.info("热点自启同步：配置=%s，已 %s", enabled,
                            "enable" if enabled else "disable")
            else:
                logger.warning("热点自启同步失败：%s", result.get("error"))
        else:
            logger.debug("热点自启同步：状态已一致 (enabled=%s)", enabled)
    except Exception as e:  # noqa: BLE001 - 启动同步决不能阻断主服务
        logger.warning("热点自启同步异常（忽略）: %s", e)
