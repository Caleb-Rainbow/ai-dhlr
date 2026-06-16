"""BLE 配网协议分帧/编解码单元测试（纯逻辑，两端契约一致性）。

运行：根目录下 `pytest tests/test_ble_protocol.py`
"""
import json

from src.ble.protocol import (
    DEFAULT_MTU,
    MAGIC,
    MessageDecoder,
    chunk_size,
    crc16,
    encode_frame,
    encode_message,
    build_device_info,
)


# --------------------------------------------------------------------------- #
# CRC16-CCITT-FALSE
# --------------------------------------------------------------------------- #
def test_crc16_known_vector():
    # 标准校验值：CRC16-CCITT-FALSE("123456789") == 0x29B1
    assert crc16(b"123456789") == 0x29B1


def test_crc16_empty_is_init():
    assert crc16(b"") == 0xFFFF


def test_crc16_deterministic():
    assert crc16(b"hello") == crc16(b"hello")


# --------------------------------------------------------------------------- #
# encode_frame
# --------------------------------------------------------------------------- #
def test_encode_frame_structure_and_crc():
    payload = b'{"cmd":"apply"}'
    frame = encode_frame(7, payload)
    assert frame[0] == MAGIC
    # 头部 5 + payload + crc 2
    assert len(frame) == 5 + len(payload) + 2
    # 末 2 字节 == 对 magic..payload 的 crc
    body = frame[:-2]
    assert frame[-2:] == int(crc16(body)).to_bytes(2, "big")
    # seq 回读
    assert int.from_bytes(frame[1:3], "big") == 7
    assert int.from_bytes(frame[3:5], "big") == len(payload)


# --------------------------------------------------------------------------- #
# FrameDecoder
# --------------------------------------------------------------------------- #
def test_decoder_whole_frame():
    payload = b'{"a":1}'
    frame = encode_frame(3, payload)
    out = MessageDecoder().feed(frame)
    assert out == [(3, {"a": 1})]


def test_decoder_byte_by_byte():
    payload = b'{"k":"v"}'
    frame = encode_frame(11, payload)
    dec = MessageDecoder()
    received = []
    for b in frame:
        received.extend(dec.feed(bytes([b])))
    assert received == [(11, {"k": "v"})]


def test_decoder_multi_frame_concatenated():
    f1 = encode_message(1, {"cmd": "scan_wifi"}, mtu=DEFAULT_MTU)
    f2 = encode_message(2, {"cmd": "apply"}, mtu=DEFAULT_MTU)
    blob = b"".join(f1) + b"".join(f2)
    out = MessageDecoder().feed(blob)
    assert [seq for seq, _ in out] == [1, 2]
    assert out[0][1]["cmd"] == "scan_wifi"
    assert out[1][1]["cmd"] == "apply"


def test_decoder_resync_after_garbage():
    dec = MessageDecoder()
    # 前导垃圾（无 magic）应被丢弃
    assert dec.feed(b"\x00\x01\x02garbage") == []
    # 随后的合法帧正常解析
    frame = b"".join(encode_message(9, {"cmd": "cancel"}))
    out = dec.feed(frame)
    assert out == [(9, {"cmd": "cancel"})]


def test_decoder_drops_bad_crc():
    dec = MessageDecoder()
    frame = encode_frame(5, b'{"x":2}')
    corrupted = bytearray(frame)
    corrupted[-1] ^= 0xFF  # 破坏 crc
    assert dec.feed(bytes(corrupted)) == []
    # 后续合法帧仍可解析
    good = b"".join(encode_message(6, {"ok": True}))
    out = dec.feed(good)
    assert out == [(6, {"ok": True})]


# --------------------------------------------------------------------------- #
# encode_message 分块（小 MTU / 大载荷）
# --------------------------------------------------------------------------- #
def test_large_payload_small_mtu_roundtrip():
    networks = [{"ssid": f"AP-{i:03d}", "rssi": -50 - (i % 40), "security": "wpa2"} for i in range(60)]
    obj = {"event": "scan_results", "id": 42, "networks": networks}
    chunks = encode_message(1, obj, mtu=30)  # chunk_size=27，必然多块
    assert len(chunks) > 1
    out = MessageDecoder().feed(b"".join(chunks))
    assert len(out) == 1
    seq, parsed = out[0]
    assert seq == 1
    assert parsed["event"] == "scan_results"
    assert parsed["networks"] == networks
    assert len(parsed["networks"]) == 60


def test_chunk_size_math():
    assert chunk_size(247) == 244
    assert chunk_size(23) == 20  # 不低于 20


# --------------------------------------------------------------------------- #
# DEVICE_INFO 裸 JSON
# --------------------------------------------------------------------------- #
def test_build_device_info_is_raw_json():
    raw = build_device_info(type="dhlr", model="rk3568", fw="0.2.0", hwid="abcd", name="AI动火离人")
    parsed = json.loads(raw.decode("utf-8"))
    assert parsed["type"] == "dhlr"
    assert parsed["proto"] == 1
    assert parsed["name"] == "AI动火离人"
