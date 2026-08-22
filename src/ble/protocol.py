"""BLE 配网协议 —— 分帧 / CRC / 消息编解码（设备侧实现）。

与 Android `ble/framing` + `provisioning/protocol` 实现**同一份契约**
（见 docs/ble-provisioning-protocol.md）。纯逻辑、无蓝牙依赖，可单测。

帧格式（大端）：
    [magic:1=0xA5][seq:2][len:2][payload:len][crc16:2]
    crc16 = CRC16-CCITT-FALSE，覆盖 magic..payload（不含 crc 自身）。
整帧字节按 (MTU-3) 切块在 REQUEST(write)/STATUS(notify) 上传输。
"""
from __future__ import annotations

import json
import struct
from typing import Any, List, Tuple

# --------------------------------------------------------------------------- #
# UUID 与版本
# --------------------------------------------------------------------------- #
SERVICE_UUID = "7d408e67-4b7d-8e1a-9b3c-2f1a00000001"
DEVICE_INFO_UUID = "7d408e67-4b7d-8e1a-9b3c-2f1a00000002"
REQUEST_UUID = "7d408e67-4b7d-8e1a-9b3c-2f1a00000003"
STATUS_UUID = "7d408e67-4b7d-8e1a-9b3c-2f1a00000004"
AUTH_UUID = "7d408e67-4b7d-8e1a-9b3c-2f1a00000005"  # 预留鉴权，开发期不用

PROTOCOL_VERSION = 1

# --------------------------------------------------------------------------- #
# CRC16-CCITT-FALSE（poly 0x1021, init 0xFFFF, 无反射, xorout 0x0000）
# 标准校验值：crc16(b"123456789") == 0x29B1
# --------------------------------------------------------------------------- #
MAGIC = 0xA5
_HEADER = struct.Struct(">BHH")  # magic, seq, len
_CRC = struct.Struct(">H")
_HEADER_SIZE = _HEADER.size  # 5
_CRC_SIZE = _CRC.size  # 2


def crc16(data: bytes, init: int = 0xFFFF) -> int:
    """CRC16-CCITT-FALSE。"""
    crc = init
    for byte in data:
        crc ^= byte << 8
        for _ in range(8):
            if crc & 0x8000:
                crc = ((crc << 1) ^ 0x1021) & 0xFFFF
            else:
                crc = (crc << 1) & 0xFFFF
    return crc


# --------------------------------------------------------------------------- #
# 分帧
# --------------------------------------------------------------------------- #
ATT_OVERHEAD = 3
DEFAULT_MTU = 247
MAX_PAYLOAD = 0xFFFF  # len 字段 u16 上限
MAX_FRAME_PAYLOAD = 8192  # 单帧 payload 实用上限，防损坏的 len 字段造成长时间挂起


def chunk_size(mtu: int) -> int:
    """单个 BLE 包可承载的有效字节（MTU - ATT 头）。"""
    return max(20, mtu - ATT_OVERHEAD)


def encode_frame(seq: int, payload: bytes) -> bytes:
    """编码单帧：magic|seq|len|payload|crc16。"""
    # 发送端也按 MAX_FRAME_PAYLOAD（接收端硬限）校验：否则接收端 FrameDecoder 会静默丢帧
    # （length > MAX_FRAME_PAYLOAD → 丢 magic 重同步），致对端 rpc/image 等待器超时无报错。
    if len(payload) > MAX_FRAME_PAYLOAD:
        raise ValueError(f"payload 过大: {len(payload)} > {MAX_FRAME_PAYLOAD}")
    if not (0 <= seq <= 0xFFFF):
        raise ValueError(f"seq 越界: {seq}")
    body = _HEADER.pack(MAGIC, seq, len(payload)) + payload
    return body + _CRC.pack(crc16(body))


def chunk_frame(frame: bytes, mtu: int) -> List[bytes]:
    """将整帧按 MTU 切成有序块。"""
    size = chunk_size(mtu)
    return [frame[i : i + size] for i in range(0, len(frame), size)] or [b""]


def encode_message(seq: int, obj: Any, mtu: int = DEFAULT_MTU) -> List[bytes]:
    """对象 → JSON → 帧 → 切块（用于 write/notify 载荷）。"""
    payload = json.dumps(obj, ensure_ascii=False).encode("utf-8")
    return chunk_frame(encode_frame(seq, payload), mtu)


# --------------------------------------------------------------------------- #
# 解帧（流式重组，支持多帧拼接、坏帧重同步）
# --------------------------------------------------------------------------- #
class FrameDecoder:
    """喂入按序到达的块字节，吐出完整且 CRC 校验通过的 (seq, payload)。

    遇到非法 magic 或 CRC 错误时丢弃该字节并尝试重同步（从下一个 magic 起），
    保证收端不会被一个坏帧永久卡住。
    """

    def __init__(self) -> None:
        self._buf = bytearray()

    def feed(self, chunk: bytes) -> List[Tuple[int, bytes]]:
        self._buf.extend(chunk)
        out: List[Tuple[int, bytes]] = []
        while True:
            frame, consumed, ok = self._try_parse_one()
            # 丢弃本次尝试消耗掉的无用前缀字节（重同步用）
            if consumed:
                del self._buf[:consumed]
            if ok and frame is not None:
                out.append(frame)
                continue
            # 未消耗或未就绪（数据不足）
            break
        return out

    def _try_parse_one(self) -> Tuple[Any, int, bool]:
        """返回 (frame|None, 已消耗字节数, 是否成功解析一帧)。"""
        # 丢弃前导非 magic 字节（初始同步 / 垃圾）
        skip = 0
        while skip < len(self._buf) and self._buf[skip] != MAGIC:
            skip += 1
        if skip:
            return None, skip, False
        # 此时 buf[0] == MAGIC（或数据不足）
        if len(self._buf) < _HEADER_SIZE:
            return None, 0, False
        _, seq, length = _HEADER.unpack_from(self._buf, 0)
        if length > MAX_FRAME_PAYLOAD:
            # 损坏的 len 字段，丢掉 magic 字节重新找同步
            return None, 1, False
        total = _HEADER_SIZE + length + _CRC_SIZE
        # 整帧未到齐
        if len(self._buf) < total:
            return None, 0, False
        body = bytes(self._buf[: _HEADER_SIZE + length])
        (crc_got,) = _CRC.unpack_from(self._buf, _HEADER_SIZE + length)
        if crc16(body) != crc_got:
            # CRC 错：按帧边界丢弃整帧（协议要求对端重发），保持后续帧对齐
            return None, total, False
        payload = bytes(self._buf[_HEADER_SIZE : _HEADER_SIZE + length])
        return (seq, payload), total, True


class MessageDecoder:
    """FrameDecoder + JSON 解析：喂块，吐 (seq, obj)。坏 JSON 帧被丢弃。"""

    def __init__(self) -> None:
        self._frames = FrameDecoder()

    def feed(self, chunk: bytes) -> List[Tuple[int, Any]]:
        out: List[Tuple[int, Any]] = []
        for seq, payload in self._frames.feed(chunk):
            try:
                out.append((seq, json.loads(payload.decode("utf-8"))))
            except (ValueError, UnicodeDecodeError):
                continue
        return out


# --------------------------------------------------------------------------- #
# DEVICE_INFO（裸 JSON，直读，不分帧）
# --------------------------------------------------------------------------- #
def build_device_info(
    *,
    type: str,
    model: str,
    fw: str,
    hwid: str,
    name: str,
    proto: int = PROTOCOL_VERSION,
    network: Any = None,
) -> bytes:
    """生成 DEVICE_INFO 特征值的裸 JSON 字节。network 为当前网络状态(可选)。"""
    data: dict = {"type": type, "model": model, "fw": fw, "hwid": hwid, "name": name, "proto": proto}
    if network is not None:
        data["network"] = network
    # bless 0.3.0 的 ReadValue 未处理 offset，DEVICE_INFO 应尽量落在一次 GATT Read
    # 响应内；紧凑 JSON 保持协议内容不变，同时降低触发 Android Read Blob 的概率。
    return json.dumps(data, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
