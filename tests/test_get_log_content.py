"""_get_log_content 安全校验单测：路径穿越拒绝 + 字节上限截断。

BLE 暴露 get_log_content 后，filename 是不可信 wire 输入（无鉴权链路）。本测试固化
「../../etc/passwd 等穿越串被拒、绝不读出 log_dir 外文件」+「超 6KB 截断保留最新」两条不变式。
"""
import asyncio
from unittest.mock import patch

import pytest

from src.api.ws_handler import WSHandler


@pytest.fixture
def handler():
    return WSHandler()


def test_get_log_content_normal(handler, tmp_path):
    log = tmp_path / "fire_safety_2026-07-09.log"
    log.write_text("line-a\nline-b\nline-c\n", encoding="utf-8")
    with patch("src.utils.logger.event_logger._log_dir", tmp_path):
        result = asyncio.run(handler._get_log_content({"filename": "fire_safety_2026-07-09.log"}))
    assert result["filename"] == "fire_safety_2026-07-09.log"
    assert "line-c" in result["content"]
    assert result["truncated"] is False
    assert result["total_pages"] >= 1


@pytest.mark.parametrize("bad_name", [
    "../../etc/passwd",
    "..\\..\\windows\\win.ini",
    "/etc/passwd",
    "subdir/x.log",
    "fire_safety.log/../../../etc/passwd",
])
def test_get_log_content_path_traversal_rejected(handler, tmp_path, bad_name):
    """任何含分隔符/前导点的 filename 必须抛 ValueError，绝不读出 log_dir 外文件。"""
    (tmp_path / "fire_safety_2026-07-09.log").write_text("ok\n", encoding="utf-8")
    with patch("src.utils.logger.event_logger._log_dir", tmp_path):
        with pytest.raises(ValueError):
            asyncio.run(handler._get_log_content({"filename": bad_name}))


def test_get_log_content_bad_suffix_rejected(handler, tmp_path):
    (tmp_path / "secret.txt").write_text("hidden\n", encoding="utf-8")
    with patch("src.utils.logger.event_logger._log_dir", tmp_path):
        with pytest.raises(ValueError):
            asyncio.run(handler._get_log_content({"filename": "secret.txt"}))


def test_get_log_content_truncated_to_max(handler, tmp_path):
    """单页超 6KB 必须截断到末尾（保留最新）且 truncated=True，字节 ≤ 6144（< BLE 8192 单帧）。"""
    log = tmp_path / "fire_safety_2026-07-09.log"
    # 200 行 × 每行 ~40B ≈ 8KB（> 6KB），触发截断
    log.write_text("".join(f"2026-07-09 10:00:{i:02d} [INFO] padding-line-{i:03d}-xxxxxxxx\n" for i in range(200)), encoding="utf-8")
    with patch("src.utils.logger.event_logger._log_dir", tmp_path):
        result = asyncio.run(handler._get_log_content({"filename": "fire_safety_2026-07-09.log", "page_size": 200}))
    assert result["truncated"] is True
    assert len(result["content"].encode("utf-8")) <= 6144
    # 保留最新：最后一行（line-199）必须在
    assert "line-199" in result["content"]


def test_get_log_content_defaults_latest(handler, tmp_path):
    """filename 缺省取最新文件（按名倒序）。"""
    (tmp_path / "fire_safety_2026-07-08.log").write_text("old\n", encoding="utf-8")
    (tmp_path / "fire_safety_2026-07-09.log").write_text("new\n", encoding="utf-8")
    with patch("src.utils.logger.event_logger._log_dir", tmp_path):
        result = asyncio.run(handler._get_log_content({}))
    assert result["filename"] == "fire_safety_2026-07-09.log"
