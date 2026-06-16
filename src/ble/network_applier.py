"""WiFi 扫描与切网（root，经 nmcli，无 polkit/sudo）。

由 ai-dhlr-ble.service 调用：
- scan()         扫描周边 WiFi，返回 [{ssid, rssi, security}]（供手机选择）。
- connect(ssid, password)  关热点 → 起 STA 连接 → 取 IP；失败自动回退热点。

nmcli 调用经可注入的 runner，便于单测（解析/映射/命令构造为纯逻辑）。
"""
from __future__ import annotations

import logging
import time
from typing import Callable, List, Optional, Tuple

logger = logging.getLogger(__name__)

PROVISIONED_CONN = "provisioned-wifi"
DEFAULT_IFACE = "wlan0"

# nmcli 子进程执行器：(cmd) -> (returncode, combined_output)
Runner = Callable[[List[str]], Tuple[int, str]]


def _default_runner(cmd: List[str]) -> Tuple[int, str]:
    import subprocess

    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
    return proc.returncode, (proc.stdout + proc.stderr)


def map_security(raw: str) -> str:
    """nmcli SECURITY 字段 → 协议枚举字符串。"""
    s = (raw or "").strip()
    if s in ("", "--"):
        return "open"
    if "WPA3" in s:
        return "wpa3"
    if "WPA2" in s:
        return "wpa2"
    if "WPA" in s:  # 含 WPA1
        return "wpa"
    if "WEP" in s:
        return "wep"
    return "unknown"


def parse_scan_lines(lines: List[str]) -> List[dict]:
    """解析 `nmcli -t -f SSID,SIGNAL,SECURITY device wifi list` 的输出行。

    按ssid:signal:security 冒号分隔；同 SSID 取信号最强；空 SSID（隐藏）跳过。
    """
    best: dict[str, dict] = {}
    for line in lines:
        line = line.strip()
        if not line:
            continue
        parts = line.split(":")
        if len(parts) < 2:
            continue
        ssid = parts[0]
        if not ssid or ssid == "--":
            continue  # 隐藏网络，列表不展示
        try:
            rssi_signal = int(parts[1])
        except ValueError:
            continue
        security_raw = parts[2] if len(parts) > 2 else ""
        net = {"ssid": ssid, "rssi": _signal_to_dbm(rssi_signal), "security": map_security(security_raw)}
        prev = best.get(ssid)
        if prev is None or rssi_signal > prev["_signal"]:
            best[ssid] = {**net, "_signal": rssi_signal}
    # 去掉内部排序键，按信号降序
    result = [{k: v for k, v in n.items() if k != "_signal"} for n in best.values()]
    result.sort(key=lambda n: n["rssi"], reverse=True)
    return result


def _signal_to_dbm(strength: int) -> int:
    """nmcli 信号强度 0-100 → 近似 dBm（0→-100, 100→-40）。仅用于展示排序。"""
    return -100 + int(strength * 0.6)


def _extract_ip4_addr(text: str) -> Optional[str]:
    """从 `nmcli -g IP4.ADDRESS dev <iface>` 输出取首个 IPv4（去 CIDR 前缀）。"""
    for token in text.strip().split():
        addr = token.split("/")[0].strip()
        if addr and addr != "--":
            return addr
    return None


class NetworkApplier:
    """WiFi 扫描与切网。"""

    def __init__(self, runner: Runner = _default_runner, iface: str = DEFAULT_IFACE) -> None:
        self._run = runner
        self.iface = iface

    # ---------------------------- 扫描 ---------------------------- #
    def scan(self) -> List[dict]:
        rc, _ = self._run(["nmcli", "device", "wifi", "rescan"])
        if rc != 0:
            logger.warning("wifi rescan 返回 %s（继续用缓存列表）", rc)
        rc, out = self._run(
            ["nmcli", "-t", "-f", "SSID,SIGNAL,SECURITY", "device", "wifi", "list"]
        )
        if rc != 0:
            logger.error("wifi list 失败: %s", out)
            return []
        return parse_scan_lines(out.splitlines())

    # ---------------------------- 切网 ---------------------------- #
    def active_hotspot_connection(self) -> Optional[str]:
        """返回当前活动的热点连接名（若有），用于切网后回退。"""
        rc, out = self._run(
            ["nmcli", "-t", "-f", "NAME,TYPE,DEVICE,MODE", "connection", "show", "--active"]
        )
        if rc != 0:
            return None
        for line in out.splitlines():
            name, ctype, dev, mode = (line.split(":") + ["", "", "", ""])[:4]
            if ctype.startswith("802-11-wireless") and dev == self.iface and mode in ("ap", "hotspot"):
                return name
        return None

    def connect(self, ssid: str, password: str) -> dict:
        """切 wlan0 到指定 WiFi（STA）。成功 {ok, ip}；失败回退热点 {ok:false, error}。。"""
        hotspot = self.active_hotspot_connection()
        result = self._activate_sta(ssid, password, hotspot)
        if result["ok"]:
            return result
        # 回退到原热点，保证设备仍可被访问/重新配网
        if hotspot:
            logger.warning("切网失败，回退热点 %s", hotspot)
            self._run(["nmcli", "connection", "up", hotspot])
        return result

    def _activate_sta(self, ssid: str, password: str, hotspot: Optional[str]) -> dict:
        # 1) 关热点
        if hotspot:
            self._run(["nmcli", "connection", "down", hotspot])
        # 2) 建/更新 STA 连接（idempotent）
        add_cmd = [
            "nmcli", "connection", "add", "type", "wifi", "ifname", self.iface,
            "con-name", PROVISIONED_CONN, "ssid", ssid,
            "wifi-sec.key-mgmt", "wpa-psk", "wifi-sec.psk", password,
        ]
        rc, out = self._run(add_cmd)
        if rc != 0:
            # 已存在同名连接则改用 modify
            self._run([
                "nmcli", "connection", "modify", PROVISIONED_CONN,
                "ssid", ssid, "wifi-sec.key-mgmt", "wpa-psk", "wifi-sec.psk", password,
            ])
        # 3) 激活
        rc, out = self._run(["nmcli", "connection", "up", PROVISIONED_CONN])
        if rc != 0:
            return {"ok": False, "error": f"connect failed: {out.strip()[:200]}"}
        # 4) 等 IP
        ip = self._wait_for_ip(timeout=20)
        if not ip:
            return {"ok": False, "error": "dhcp timeout"}
        return {"ok": True, "ip": ip, "ssid": ssid}

    def _wait_for_ip(self, timeout: int = 20) -> Optional[str]:
        deadline = time.time() + timeout
        while time.time() < deadline:
            rc, out = self._run(["nmcli", "-g", "IP4.ADDRESS", "device", "show", self.iface])
            ip = _extract_ip4_addr(out)
            if ip:
                return ip
            time.sleep(1)
        return None
