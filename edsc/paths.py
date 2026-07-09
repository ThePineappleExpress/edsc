"""Per-user config/state directories, resolved without third-party deps.

Follows the XDG Base Directory spec on Linux, ``%APPDATA%``/``%LOCALAPPDATA%``
on Windows, and ``~/Library/Application Support`` on macOS.


    EDSC - Colonization commodities tracker
    Copyright (C) 2026  ThePineappleExpress

    This program is free software: you can redistribute it and/or modify
    it under the terms of the GNU General Public License as published by
    the Free Software Foundation, either version 3 of the License, or
    (at your option) any later version.

    This program is distributed in the hope that it will be useful,
    but WITHOUT ANY WARRANTY; without even the implied warranty of
    MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
    GNU General Public License for more details.

    You should have received a copy of the GNU General Public License
    along with this program.  If not, see <https://www.gnu.org/licenses/>.


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


def asset_path(name: str) -> Path:
    """Resolve a bundled asset, working both from source and a frozen build.

    PyInstaller unpacks bundled data into ``sys._MEIPASS`` at runtime; in a
    normal checkout the assets live next to this module in ``edsc/assets``.
    """
    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        candidate = Path(meipass) / "edsc" / "assets" / name
        if candidate.exists():
            return candidate
    return Path(__file__).resolve().parent / "assets" / name
