"""Application version loaded from the repository's single source of truth."""

from pathlib import Path


VERSION_FILE = Path(__file__).resolve().parent.parent / "VERSION"
UNKNOWN_VERSION = "0.0.0+unknown"


def _load_version() -> str:
    """Read the immutable build version, with a safe fallback for partial installs."""
    try:
        version = VERSION_FILE.read_text(encoding="utf-8").strip()
    except OSError:
        return UNKNOWN_VERSION
    return version or UNKNOWN_VERSION


__version__ = _load_version()
