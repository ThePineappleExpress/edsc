"""Per-user config/state directories, resolved without third-party deps.

Follows the XDG Base Directory spec on Linux, ``%APPDATA%``/``%LOCALAPPDATA%``
on Windows, and ``~/Library/Application Support`` on macOS.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

APP_DIRNAME = "edsc"


def _home() -> Path:
    return Path(os.path.expanduser("~"))


def config_dir() -> Path:
    """Directory for user settings (``config.json``)."""
    if sys.platform == "win32":
        base = os.environ.get("APPDATA") or (_home() / "AppData" / "Roaming")
    elif sys.platform == "darwin":
        base = _home() / "Library" / "Application Support"
    else:
        base = os.environ.get("XDG_CONFIG_HOME") or (_home() / ".config")
    path = Path(base) / APP_DIRNAME
    return path


def state_dir() -> Path:
    """Directory for cached runtime state (last-known projects, etc.)."""
    if sys.platform == "win32":
        base = os.environ.get("LOCALAPPDATA") or (_home() / "AppData" / "Local")
    elif sys.platform == "darwin":
        base = _home() / "Library" / "Application Support"
    else:
        base = os.environ.get("XDG_STATE_HOME") or (_home() / ".local" / "state")
    return Path(base) / APP_DIRNAME


def ensure_dir(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    return path
