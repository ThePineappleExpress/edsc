"""Read Elite's HUD color matrix from its graphics configuration."""

# SPDX-License-Identifier: GPL-3.0-or-later

from __future__ import annotations

import os
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

from . import ELITE_STEAM_APPID
from .journal.locator import steam_library_folders

Row = tuple[float, float, float]
Matrix = tuple[Row, Row, Row]

IDENTITY: Matrix = ((1.0, 0.0, 0.0), (0.0, 1.0, 0.0), (0.0, 0.0, 1.0))

_ROW_TAGS = ("MatrixRed", "MatrixGreen", "MatrixBlue")
_OVERRIDE_TAIL = (
    Path("Frontier Developments")
    / "Elite Dangerous"
    / "Options"
    / "Graphics"
    / "GraphicsConfigurationOverride.xml"
)
_PROTON_LOCALAPPDATA = (
    Path("pfx") / "drive_c" / "users" / "steamuser" / "AppData" / "Local"
)


def transform(matrix: Matrix, rgb: tuple[int, int, int]) -> tuple[int, int, int]:
    """Push an 8-bit RGB colour through the HUD matrix, clamped to 0..255."""
    r, g, b = (
        min(255, max(0, round(row[0] * rgb[0] + row[1] * rgb[1] + row[2] * rgb[2])))
        for row in matrix
    )
    return (r, g, b)


def _parse_rows(path: Path) -> dict[str, Row]:
    """Matrix rows present in one XML file; ``{}`` if unreadable or absent."""
    try:
        root = ET.parse(path).getroot()
    except (OSError, ET.ParseError):
        return {}
    default = root.find("GUIColour/Default")
    if default is None:
        return {}
    rows: dict[str, Row] = {}
    for tag in _ROW_TAGS:
        text = default.findtext(tag)
        if text is None:
            continue
        try:
            values = tuple(float(v) for v in text.replace(",", " ").split())
        except ValueError:
            continue
        if len(values) == 3:
            rows[tag] = values
    return rows


def _complete(rows: dict[str, Row]) -> Matrix:
    """Fill any missing rows from the identity matrix."""
    red, green, blue = (
        rows.get(tag, IDENTITY[i]) for i, tag in enumerate(_ROW_TAGS)
    )
    return (red, green, blue)


def _game_config_candidates() -> list[Path]:
    """``GraphicsConfiguration.xml`` inside Steam installs of the game."""
    candidates: list[Path] = []
    for lib in steam_library_folders():
        products = lib / "steamapps" / "common" / "Elite Dangerous" / "Products"
        try:
            versions = list(products.iterdir())
        except OSError:
            continue
        # Prefer the live (Odyssey) build over legacy Horizons product dirs.
        versions.sort(key=lambda p: (0 if "odyssey" in p.name.lower() else 1, p.name))
        for version in versions:
            config = version / "GraphicsConfiguration.xml"
            if config.is_file():
                candidates.append(config)
    return candidates


def _override_candidates(journal_dir: Path | None) -> list[Path]:
    """``GraphicsConfigurationOverride.xml`` in the user's options directory."""
    candidates: list[Path] = []
    # The journal dir sits in the same (real or Wine) user profile as the options dir, so walking its parents finds nonstandard installs too.
    if journal_dir is not None:
        for parent in Path(journal_dir).parents:
            candidates.append(parent / "AppData" / "Local" / _OVERRIDE_TAIL)
    home = Path.home()
    if sys.platform == "win32":
        base = os.environ.get("LOCALAPPDATA") or (home / "AppData" / "Local")
        candidates.append(Path(base) / _OVERRIDE_TAIL)
    else:
        for lib in steam_library_folders():
            prefix = lib / "steamapps" / "compatdata" / ELITE_STEAM_APPID
            candidates.append(prefix / _PROTON_LOCALAPPDATA / _OVERRIDE_TAIL)
    return candidates


def load_matrix(journal_dir: Path | None = None) -> Matrix | None:
    """Best-effort HUD colour matrix, or ``None`` if no config was found; ``journal_dir`` (the resolved journal directory, if known) anchors the search for the per-user override file, and auto-detected locations are probed either way."""
    env = os.environ.get("EDSC_GRAPHICS_CONFIG")
    if env:
        rows = _parse_rows(Path(env).expanduser())
        return _complete(rows) if rows else None

    rows: dict[str, Row] = {}
    for path in _game_config_candidates():
        found = _parse_rows(path)
        if found:
            rows = found
            break
    for path in _override_candidates(journal_dir):
        found = _parse_rows(path)
        if found:
            rows.update(found)
            break
    return _complete(rows) if rows else None
