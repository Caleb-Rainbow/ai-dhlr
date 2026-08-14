#!/usr/bin/env python3
"""
动火离人安全监测设备 - 局域网发现扫描脚本（客户端参考实现）

像 IP 摄像头扫描软件一样，在局域网内发现"动火离人监测"设备并打印其信息
（IP / API 端口 / 设备ID / 型号 / 固件版本 / MAC ...）。

两种用法：
  1) 主动扫描（默认）：向局域网广播发现请求，等待设备应答
       python discovery_scan.py            # 默认等 2 秒
       python discovery_scan.py -t 5       # 等 5 秒
       python discovery_scan.py --subnet 192.168.2   # 额外定向扫 192.168.2.255

  2) 被动监听：静候设备每 15s 的周期主动广播
       python discovery_scan.py --listen           # 在【另一台机器】上运行
                                                       （设备本机 32100 已被占用）

协议：客户端发 "DHLR_DISCOVER/1" -> 设备单播回 JSON。
"""
import argparse
import json
import socket
import time

MAGIC = b"DHLR_DISCOVER/1"
PORT = 32100


def scan(timeout: float = 2.0, port: int = PORT, subnet: str = None):
    """主动广播扫描，返回 [(addr, info), ...]"""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
    s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    s.settimeout(0.5)
    s.bind(("0.0.0.0", 0))

    targets = ["255.255.255.255"]
    if subnet:
        targets.append(f"{subnet}.255")
    for t in targets:
        try:
            s.sendto(MAGIC, (t, port))
        except OSError:
            pass

    seen = {}
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            data, addr = s.recvfrom(4096)
        except socket.timeout:
            continue
        try:
            info = json.loads(data.decode("utf-8"))
        except Exception:
            continue
        key = f"{info.get('device_id','')}@{addr[0]}"
        seen.setdefault(key, (addr, info))
    s.close()
    return list(seen.values())


def listen(duration: float, port: int = PORT):
    """被动监听设备的周期主动广播（须在非设备本机的机器上运行）"""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    s.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
    s.bind(("0.0.0.0", port))
    s.settimeout(1.0)
    print(f"被动监听 UDP/0.0.0.0:{port} ... (持续 {duration}s，Ctrl+C 退出)")
    seen = {}
    deadline = time.time() + duration
    try:
        while time.time() < deadline:
            try:
                data, addr = s.recvfrom(4096)
            except socket.timeout:
                continue
            try:
                info = json.loads(data.decode("utf-8"))
            except Exception:
                continue
            key = f"{info.get('device_id','')}@{addr[0]}"
            if key not in seen:
                seen[key] = (addr, info)
                print_device(addr, info)
    except KeyboardInterrupt:
        pass
    s.close()
    return list(seen.values())


def print_device(addr, info):
    print(f"\n=== 发现设备 @ {addr[0]} ===")
    print(json.dumps(info, ensure_ascii=False, indent=2))


def main():
    ap = argparse.ArgumentParser(description="动火离人监测设备局域网发现扫描")
    ap.add_argument("-t", "--timeout", type=float, default=2.0, help="主动扫描等待秒数")
    ap.add_argument("-p", "--port", type=int, default=PORT, help="UDP 端口")
    ap.add_argument("--subnet", default=None, help="额外定向广播子网，如 192.168.2")
    ap.add_argument("--listen", action="store_true", help="被动监听设备的周期主动广播")
    ap.add_argument("--listen-duration", type=float, default=30.0, help="监听时长(秒)")
    args = ap.parse_args()

    if args.listen:
        results = listen(args.listen_duration, args.port)
    else:
        results = scan(args.timeout, args.port, args.subnet)
        for addr, info in results:
            print_device(addr, info)

    print(f"\n共发现 {len(results)} 台设备")


if __name__ == "__main__":
    main()
