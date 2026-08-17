#!/usr/bin/env python3
"""Bump DHLR's SemVer and optionally commit it with already staged changes."""

from __future__ import annotations

import argparse
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path


REPOSITORY_ROOT = Path(__file__).resolve().parent.parent
VERSION_FILE = REPOSITORY_ROOT / "VERSION"
SEMVER_PATTERN = re.compile(r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)$")


@dataclass(frozen=True)
class SemVer:
    major: int
    minor: int
    patch: int

    @classmethod
    def parse(cls, value: str) -> "SemVer":
        match = SEMVER_PATTERN.fullmatch(value.strip())
        if not match:
            raise ValueError(f"无效版本号 {value!r}，必须为 MAJOR.MINOR.PATCH")
        return cls(*(int(part) for part in match.groups()))

    def bump(self, level: str) -> "SemVer":
        if level == "major":
            return SemVer(self.major + 1, 0, 0)
        if level == "minor":
            return SemVer(self.major, self.minor + 1, 0)
        if level == "patch":
            return SemVer(self.major, self.minor, self.patch + 1)
        raise ValueError(f"未知升级级别: {level}")

    def __str__(self) -> str:
        return f"{self.major}.{self.minor}.{self.patch}"


def run_git(*args: str) -> None:
    subprocess.run(["git", *args], cwd=REPOSITORY_ROOT, check=True)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="升级 VERSION；--commit 仅提交已暂存内容和 VERSION。"
    )
    parser.add_argument("level", choices=("major", "minor", "patch"))
    parser.add_argument(
        "--commit",
        action="store_true",
        help="将 VERSION 加入暂存区，并与当前已暂存代码一起提交",
    )
    parser.add_argument(
        "-m",
        "--message",
        help="提交说明；默认使用 chore(release): v<新版本>",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="只显示新版本，不修改文件或提交",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    current = SemVer.parse(VERSION_FILE.read_text(encoding="utf-8"))
    new_version = current.bump(args.level)

    if args.dry_run:
        print(f"{current} -> {new_version}")
        return 0

    VERSION_FILE.write_text(f"{new_version}\n", encoding="utf-8", newline="\n")
    print(f"版本已升级: {current} -> {new_version}")

    if args.commit:
        run_git("add", "--", "VERSION")
        message = args.message or f"chore(release): v{new_version}"
        run_git("commit", "-m", message)
        print(f"已提交 v{new_version}（未执行 tag/push）")
    else:
        print("VERSION 尚未提交。需要原子提交时请加 --commit。")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
