"""Resolve per-user config, state, and asset paths."""

# SPDX-License-Identifier: GPL-3.0-or-later

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
    else:
        base = os.environ.get("XDG_CONFIG_HOME") or (_home() / ".config")
    path = Path(base) / APP_DIRNAME
    return path


def state_dir() -> Path:
    """Directory for cached runtime state (last-known projects, etc.)."""
    if sys.platform == "win32":
        base = os.environ.get("LOCALAPPDATA") or (_home() / "AppData" / "Local")
    else:
        base = os.environ.get("XDG_STATE_HOME") or (_home() / ".local" / "state")
    return Path(base) / APP_DIRNAME


def ensure_dir(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    return path


def asset_path(name: str) -> Path:
    """Resolve a bundled asset, working both from source and a frozen build; PyInstaller unpacks bundled data into ``sys._MEIPASS`` at runtime, while a normal checkout has the assets next to this module in ``edsc/assets``."""
    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        candidate = Path(meipass) / "edsc" / "assets" / name
        if candidate.exists():
            return candidate
    return Path(__file__).resolve().parent / "assets" / name
