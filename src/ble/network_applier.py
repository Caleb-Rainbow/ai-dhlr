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


def _iface_ipv4(runner: Runner, iface: str) -> Optional[str]:
    """`ip -4 -o addr show <iface>` 取首个 IPv4。"""
    rc, out = runner(["ip", "-4", "-o", "addr", "show", iface])
    for line in out.splitlines():
        if "inet " in line:
            return line.split("inet ")[1].split()[0].split("/")[0]
    return None


def _wlan_active_mode(runner: Runner) -> tuple[Optional[str], Optional[str]]:
    """返回 wlan0 活动连接的 (mode, connection_name)。mode: infrastructure(STA)/ap(热点)。

    MODE 不是 `nmcli connection show` 的合法字段（旧代码 -f NAME,DEVICE,TYPE,MODE 让 nmcli
    报 "无效字段 MODE" 写到 stderr，runner 合并 stdout+stderr → 解析不到 wlan0 行 → 返回
    (None,None) → current_network_status 误报无网络，即便 wlan0 已联网）。故分两步：先按
    NAME,DEVICE 取 wlan0 上的活动连接名，再查该连接的 802-11-wireless.mode 属性。
    """
    rc, out = runner(["nmcli", "-t", "-f", "NAME,DEVICE", "connection", "show", "--active"])
    conn: Optional[str] = None
    for line in out.splitlines():
        name, dev = (line.split(":") + ["", ""])[:2]
        if dev == DEFAULT_IFACE:
            conn = name
            break
    if not conn:
        return None, None
    rc, out = runner(["nmcli", "-g", "802-11-wireless.mode", "connection", "show", conn])
    mode = next((ln.strip().lower() for ln in out.splitlines() if ln.strip()), "")
    return (mode or None), conn


def _wifi_ssid(runner: Runner, conn_name: Optional[str]) -> Optional[str]:
    """取 STA 连接的 SSID（nmcli 连接属性）。取首个非空行——nmcli -g 偶发重复输出同值。"""
    if not conn_name:
        return None
    rc, out = runner(["nmcli", "-g", "802-11-wireless.ssid", "connection", "show", conn_name])
    s = next((ln.strip() for ln in out.splitlines() if ln.strip()), "")
    return s if s and s != "--" else None


def current_network_status(runner: Runner = _default_runner) -> dict:
    """当前网络状态（用于 DEVICE_INFO 展示）：
    {uplink: "ethernet"|"wifi"|"none", ethernet:{ip}|null, wifi:{ip,ssid}|null, hotspot:{ip}|null}
    """
    eth_ip = _iface_ipv4(runner, "eth0")
    wlan_ip = _iface_ipv4(runner, DEFAULT_IFACE)
    mode, conn_name = _wlan_active_mode(runner)

    wifi = None
    hotspot = None
    if mode == "infrastructure" and wlan_ip:
        wifi = {"ip": wlan_ip, "ssid": _wifi_ssid(runner, conn_name)}
    elif mode in ("ap", "hotspot") and wlan_ip:
        hotspot = {"ip": wlan_ip}

    ethernet = {"ip": eth_ip} if eth_ip else None
    uplink = "wifi" if wifi else ("ethernet" if ethernet else "none")
    return {"uplink": uplink, "ethernet": ethernet, "wifi": wifi, "hotspot": hotspot}


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
        """返回当前活动的热点连接名（若有），用于切网后回退。

        MODE 不是 `nmcli connection show` 的合法字段（见 _wlan_active_mode 注释），
        旧实现 `-f NAME,TYPE,DEVICE,MODE` 恒失败返回 None——热点从不被关、切网失败
        也从不回退热点。复用 _wlan_active_mode 的两步法：先取 wlan0 活动连接名，
        再查其 802-11-wireless.mode。
        """
        mode, conn = _wlan_active_mode(self._run)
        if conn and mode in ("ap", "hotspot"):
            return conn
        return None

    def _delete_provisioned_duplicates(self) -> None:
        """删除所有名为 provisioned-wifi 的旧 profile（按 UUID 逐个删）。

        nmcli connection add 不按 con-name 查重（UUID 才是主键），每次配网都会
        新增一个同名 profile；而 connection up <name> 命中同名中的第一个。一旦
        某个早期同名 profile 密码错误/缺失，之后所有配网（哪怕密码正确）都会
        激活那个坏 profile，报「连接激活失败：需要密钥，但未提供」。真机曾因此
        累积 14 个同名 profile、配网持续失败。新建前先清光同名，保证 up 无歧义。
        若 wlan0 正经旧 provisioned-wifi 上网，删除会短暂断网，随后的 up 会重连。
        """
        rc, out = self._run(["nmcli", "-t", "-f", "NAME,UUID", "connection", "show"])
        if rc != 0:
            logger.warning("清理同名 profile 失败(忽略): %s", out.strip()[:120])
            return
        for line in out.splitlines():
            name, uuid = (line.split(":") + ["", ""])[:2]
            if name == PROVISIONED_CONN and uuid:
                self._run(["nmcli", "connection", "delete", uuid])

    def _target_security(self, ssid: str) -> str:
        """查目标 SSID 当前广播的加密类型（用 nmcli 缓存列表，不触发 rescan）。

        扫不到（隐藏网络/刚消失）返回 "unknown"，调用方按加密网络处理（要求密码），
        宁可失败也不建出无 psk 的 profile。
        """
        rc, out = self._run(["nmcli", "-t", "-f", "SSID,SECURITY", "device", "wifi", "list"])
        if rc != 0:
            return "unknown"
        for line in out.splitlines():
            parts = line.split(":")
            if parts[0] == ssid:
                return map_security(parts[1] if len(parts) > 1 else "")
        return "unknown"

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
        # 0) 加密网络空密码直接失败：App 端空密码不上送 password 字段（开放网络语义），
        #    设备端若照建 profile 会得到 key-mgmt=wpa-psk 但无 psk 的坏件，up 报
        #    「需要密钥，但未提供」。按扫描到的加密类型区分：开放网络不写 wifi-sec。
        security = self._target_security(ssid)
        secured = security != "open"
        if secured and not password:
            return {"ok": False, "error": f"wifi password required (ssid={ssid}, security={security})"}
        # 1) 关热点
        if hotspot:
            self._run(["nmcli", "connection", "down", hotspot])
        # 2) 清掉历史同名 profile，再新建（add 不查重名，见 _delete_provisioned_duplicates）
        self._delete_provisioned_duplicates()
        add_cmd = [
            "nmcli", "connection", "add", "type", "wifi", "ifname", self.iface,
            "con-name", PROVISIONED_CONN, "ssid", ssid,
        ]
        if secured:
            add_cmd += ["wifi-sec.key-mgmt", "wpa-psk", "wifi-sec.psk", password]
        rc, out = self._run(add_cmd)
        if rc != 0:
            # 已存在同名连接则改用 modify（正常不会发生：上面已清光同名）；两段错误都带回
            rc2, out2 = self._run([
                "nmcli", "connection", "modify", PROVISIONED_CONN,
                "ssid", ssid, "wifi-sec.key-mgmt", "wpa-psk", "wifi-sec.psk", password,
            ])
            if rc2 != 0:
                detail = f"add: {out.strip()[:120]} | modify: {out2.strip()[:120]}"
                return {"ok": False, "error": f"connection setup failed: {detail}"}
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
