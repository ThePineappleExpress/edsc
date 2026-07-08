"""Locate the Elite Dangerous journal directory across platforms.

Resolution order:

1. Explicit override (``EDSC_JOURNAL_DIR`` env var, or a value passed in).
2. Platform-native default location.
3. On Linux, the game usually runs under Steam Proton, so we probe the Wine
   prefix for AppID 359320 in both Flatpak and native Steam layouts, including
   extra Steam library folders parsed from ``libraryfolders.vdf``.

A directory only counts as a match if it actually contains at least one
``Journal.*.log`` file (or, failing that, exists and looks like the right
folder), so a stale/half-installed prefix does not win over a real one.


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
import re
import sys
from pathlib import Path

from .. import ELITE_STEAM_APPID

_JOURNAL_GLOB = "Journal.*.log"
_SAVED_GAMES_TAIL = Path("Saved Games") / "Frontier Developments" / "Elite Dangerous"
_PROTON_USER_TAIL = (
    Path("pfx") / "drive_c" / "users" / "steamuser" / _SAVED_GAMES_TAIL
)


def _has_journals(path: Path) -> bool:
    try:
        return any(path.glob(_JOURNAL_GLOB))
    except OSError:
        return False


def _steam_roots() -> list[Path]:
    """Candidate Steam install roots on Linux (Flatpak + native + Snap)."""
    home = Path.home()
    roots = [
        # Flatpak Steam (com.valvesoftware.Steam)
        home / ".var/app/com.valvesoftware.Steam/.local/share/Steam",
        home / ".var/app/com.valvesoftware.Steam/data/Steam",
        # Native Steam
        home / ".local/share/Steam",
        home / ".steam/steam",
        home / ".steam/root",
        # Snap Steam
        home / "snap/steam/common/.local/share/Steam",
    ]
    # De-dup while preserving order.
    seen: set[Path] = set()
    out: list[Path] = []
    for r in roots:
        if r not in seen:
            seen.add(r)
            out.append(r)
    return out


def _library_folders(steam_root: Path) -> list[Path]:
    """Parse ``steamapps/libraryfolders.vdf`` for extra library paths."""
    vdf = steam_root / "steamapps" / "libraryfolders.vdf"
    libs = [steam_root]
    try:
        text = vdf.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return libs
    # Grab every "path" "<value>" entry; robust enough for both vdf schema versions.
    for m in re.finditer(r'"path"\s*"([^"]+)"', text):
        p = Path(m.group(1))
        if p not in libs:
            libs.append(p)
    return libs


def _proton_candidates() -> list[Path]:
    """All plausible Proton-prefix journal dirs for Elite Dangerous."""
    candidates: list[Path] = []
    for root in _steam_roots():
        for lib in _library_folders(root):
            prefix = (
                lib / "steamapps" / "compatdata" / ELITE_STEAM_APPID / _PROTON_USER_TAIL
            )
            candidates.append(prefix)
    return candidates


def platform_candidates() -> list[Path]:
    """Ordered list of default journal-dir candidates for this OS."""
    home = Path.home()
    if sys.platform == "win32":
        userprofile = Path(os.environ.get("USERPROFILE", str(home)))
        return [userprofile / _SAVED_GAMES_TAIL]
    if sys.platform == "darwin":
        return [
            home / "Library/Application Support/Frontier Developments/Elite Dangerous",
            # CrossOver/Wine bottle fallback.
            home
            / "Library/Application Support/CrossOver/Bottles/EliteDangerous/drive_c/users/crossover"
            / _SAVED_GAMES_TAIL,
        ]
    # Linux (and anything else): Proton prefixes are the norm.
    return _proton_candidates()


def find_journal_dir(override: str | os.PathLike[str] | None = None) -> Path | None:
    """Return the best journal directory, or ``None`` if none is found.

    ``override`` (or ``$EDSC_JOURNAL_DIR``) takes precedence and is returned as
    long as it exists, even if empty -- an explicit choice is always honoured.
    """
    override = override or os.environ.get("EDSC_JOURNAL_DIR")
    if override:
        p = Path(override).expanduser()
        if p.is_dir():
            return p

    candidates = platform_candidates()
    # Prefer a candidate that already has journals; fall back to first existing.
    for c in candidates:
        if _has_journals(c):
            return c
    for c in candidates:
        if c.is_dir():
            return c
    return None


def latest_journal(journal_dir: Path) -> Path | None:
    """Newest ``Journal.*.log`` in a directory, by embedded timestamp then mtime."""
    files = list(journal_dir.glob(_JOURNAL_GLOB))
    if not files:
        return None
    # Filenames sort chronologically (Journal.<ISO-ish>.<part>.log); mtime breaks ties.
    return max(files, key=lambda p: (p.name, p.stat().st_mtime))


def all_journals(journal_dir: Path) -> list[Path]:
    """All ``Journal.*.log`` files, oldest first."""
    return sorted(journal_dir.glob(_JOURNAL_GLOB), key=lambda p: p.name)
