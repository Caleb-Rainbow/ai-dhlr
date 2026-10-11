"""蓝牙 IPv4 配置校验及 NetworkManager 参数，校验不执行任何网络动作。"""
from ipaddress import IPv4Address, IPv4Network


def _address(value, label: str) -> IPv4Address:
    if not isinstance(value, str):
        raise ValueError(f"{label} must be an IPv4 address")
    try:
        address = IPv4Address(value.strip())
    except ValueError as exc:
        raise ValueError(f"invalid {label}") from exc
    if int(address) >> 24 in (0, 127) or address.is_multicast or int(address) >> 24 >= 240:
        raise ValueError(f"invalid {label}: unicast address required")
    return address


def validate_ipv4(config=None) -> dict:
    if config is None:
        return {"mode": "dhcp"}
    if not isinstance(config, dict):
        raise ValueError("ipv4 must be an object")
    mode = config.get("mode")
    if mode == "dhcp":
        return {"mode": "dhcp"}
    if mode != "static":
        raise ValueError("ipv4.mode must be dhcp or static")
    address = _address(config.get("address"), "IP address")
    mask = config.get("netmask")
    if not isinstance(mask, str) or len(mask.strip().split(".")) != 4:
        raise ValueError("invalid netmask")
    try:
        mask_value = int(IPv4Address(mask.strip()))
        inverted = (~mask_value) & 0xffffffff
        if not mask_value or inverted & (inverted + 1):
            raise ValueError("non-contiguous netmask")
        network = IPv4Network(f"{address}/{mask.strip()}", strict=False)
    except ValueError as exc:
        raise ValueError("invalid netmask") from exc
    if network.prefixlen < 31 and address in (network.network_address, network.broadcast_address):
        raise ValueError("IP address cannot be the network or broadcast address")
    gateway = config.get("gateway", "")
    if not isinstance(gateway, str):
        raise ValueError("invalid gateway")
    if gateway.strip():
        gw = _address(gateway, "gateway")
        if gw not in network or gw == address or (
            network.prefixlen < 31 and gw in (network.network_address, network.broadcast_address)
        ):
            raise ValueError("gateway must be another host in the same subnet")
        gateway = str(gw)
    else:
        gateway = ""
    dns = config.get("dns", [])
    if not isinstance(dns, list) or len(dns) > 4:
        raise ValueError("dns must be a list of at most 4 IPv4 addresses")
    dns = list(dict.fromkeys(str(_address(item, "DNS")) for item in dns))
    return {"mode": mode, "address": str(address), "netmask": str(network.netmask),
            "gateway": gateway, "dns": dns}


def nmcli_ipv4_args(config: dict) -> list[str]:
    """DHCP 清空上次静态值，防止切回 auto 后残留地址/路由/DNS。"""
    static = config["mode"] == "static"
    address = ""
    if static:
        prefix = IPv4Network(f"0.0.0.0/{config['netmask']}").prefixlen
        address = f"{config['address']}/{prefix}"
    return ["ipv4.method", "manual" if static else "auto",
            "ipv4.addresses", address, "ipv4.gateway", config.get("gateway", "") if static else "",
            "ipv4.dns", ",".join(config.get("dns", [])) if static else "",
            "ipv4.ignore-auto-dns", "yes" if static else "no", "ipv4.may-fail", "no"]


def validate_network_config(config: dict) -> None:
    """完整载荷先验证，再允许更新应用配置或切网。"""
    wifi, ethernet = config.get("wifi"), config.get("ethernet")
    if wifi is not None and ethernet is not None:
        raise ValueError("configure only one interface at a time")
    if wifi is not None:
        if not isinstance(wifi, dict) or not isinstance(wifi.get("ssid"), str) or not wifi["ssid"].strip():
            raise ValueError("wifi.ssid is required")
        if not isinstance(wifi.get("password", ""), str):
            raise ValueError("wifi.password must be a string")
        validate_ipv4(wifi.get("ipv4"))
    if ethernet is not None:
        if not isinstance(ethernet, dict) or not isinstance(ethernet.get("ipv4"), dict):
            raise ValueError("ethernet.ipv4 is required")
        validate_ipv4(ethernet["ipv4"])
