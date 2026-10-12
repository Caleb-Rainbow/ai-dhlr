#!/usr/bin/env python3
"""Migrate BlueZ settings without replacing device-specific configuration."""

from __future__ import annotations

import argparse
import configparser
import os
from pathlib import Path
import re
import shutil
import stat
import subprocess
import tempfile
from typing import Callable
import uuid


def _validate(text: str) -> configparser.ConfigParser:
    parser = configparser.ConfigParser(interpolation=None, strict=False, delimiters=("=",))
    parser.optionxform = str
    try:
        parser.read_string(text)
    except configparser.Error as exc:
        # Do not print configuration contents, which may include private values.
        raise ValueError("BlueZ 配置格式无效，未修改文件") from exc
    return parser


def configure_content(original: bytes) -> bytes:
    """Keep comments, other options and line endings; change only this General key."""
    bom = b"\xef\xbb\xbf" if original.startswith(b"\xef\xbb\xbf") else b""
    text = original[len(bom):].decode("utf-8")
    _validate(text)
    newline = "\r\n" if "\r\n" in text else "\n"
    lines = text.splitlines(keepends=True)
    section = ""
    general_index = None
    found = False
    for index, line in enumerate(lines):
        header = re.fullmatch(r"\s*\[([^\]]+)\]\s*", line)
        if header:
            section = header.group(1)
            if section == "General" and general_index is None:
                general_index = index
        elif section == "General":
            key = re.fullmatch(r"([ \t]*ReverseServiceDiscovery[ \t]*=[ \t]*)([^\r\n]*)(\r?\n)?", line)
            if key:
                found = True
                if key.group(2).strip() != "false":
                    lines[index] = key.group(1) + "false" + (key.group(3) or "")
    if not found:
        setting = "ReverseServiceDiscovery=false" + newline
        if general_index is None:
            if lines and not lines[-1].endswith("\n"):
                lines[-1] += newline
            lines.extend(["[General]" + newline, setting])
        else:
            if not lines[general_index].endswith("\n"):
                lines[general_index] += newline
            lines.insert(general_index + 1, setting)
    updated = "".join(lines)
    if _validate(updated).get("General", "ReverseServiceDiscovery") != "false":
        raise ValueError("BlueZ 配置迁移校验失败，未修改文件")
    return bom + updated.encode("utf-8")


def _replace(path: Path, content: bytes, metadata: os.stat_result | None) -> None:
    """Write beside the target then atomically replace it, retaining owner and mode."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = None
    try:
        with tempfile.NamedTemporaryFile(dir=path.parent, prefix=".bluez-", delete=False) as handle:
            temp_path = Path(handle.name)
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temp_path, stat.S_IMODE(metadata.st_mode) if metadata else 0o644)
        if metadata and hasattr(os, "chown"):
            os.chown(temp_path, metadata.st_uid, metadata.st_gid)
        os.replace(temp_path, path)
    finally:
        if temp_path and temp_path.exists():
            temp_path.unlink()


def migrate(path: Path, restart: Callable[[], None] | None = None) -> Path | None:
    """Return the backup on change; restart failure restores the previous config."""
    # Follow a configured symlink, preserving the symlink itself.
    path = path.resolve()
    existed = path.exists()
    metadata = path.stat() if existed else None
    original = path.read_bytes() if existed else b""
    updated = configure_content(original)
    if updated == original:
        print("[SKIP] BlueZ ReverseServiceDiscovery=false 已配置")
        return None
    backup = None
    if existed:
        backup = path.with_name(path.name + ".dhlr-backup-" + uuid.uuid4().hex)
        shutil.copy2(path, backup)
    _replace(path, updated, metadata)
    print(f"[OK]   BlueZ ReverseServiceDiscovery=false 已写入；备份: {backup or '原文件不存在'}")
    if restart:
        try:
            restart()
        except Exception as exc:
            if existed:
                _replace(path, original, metadata)
            else:
                path.unlink()
            try:
                restart()
            except Exception:
                print("[WARN] 原配置已恢复，但 bluetooth.service 仍未启动，请检查服务日志")
            raise RuntimeError("bluetooth.service 重启失败，已恢复原配置，可重新执行部署") from exc
        print("[OK]   bluetooth.service 已重启，新配置生效")
    return backup


def main() -> int:
    parser = argparse.ArgumentParser(description="幂等迁移 DHLR 所需的 BlueZ 配置")
    parser.add_argument("--config", type=Path, default=Path("/etc/bluetooth/main.conf"))
    parser.add_argument("--restart-if-changed", action="store_true")
    args = parser.parse_args()

    def restart() -> None:
        subprocess.run(["systemctl", "restart", "bluetooth.service"], check=True, timeout=30)

    try:
        migrate(args.config, restart if args.restart_if_changed else None)
    except (OSError, UnicodeError, ValueError, RuntimeError) as exc:
        print(f"[FAIL] BlueZ 配置迁移失败：{exc}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
