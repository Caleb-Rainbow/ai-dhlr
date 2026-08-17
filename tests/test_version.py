"""Tests for the repository version source and release bump rules."""

from scripts.release import SemVer
from src.version import VERSION_FILE, __version__


def test_runtime_version_matches_version_file():
    assert __version__ == VERSION_FILE.read_text(encoding="utf-8").strip()


def test_semver_bump_rules():
    version = SemVer.parse("0.2.3")

    assert str(version.bump("patch")) == "0.2.4"
    assert str(version.bump("minor")) == "0.3.0"
    assert str(version.bump("major")) == "1.0.0"
