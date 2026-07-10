"""Read the player's HUD colour matrix from Elite's graphics configuration.

The game tints its whole HUD through a 3x3 colour matrix defined under
``GUIColour/Default`` in ``GraphicsConfiguration.xml`` (shipped inside the
game folder) and optionally overridden per row in the user's
``GraphicsConfigurationOverride.xml``.  Each output channel is a weighted sum
of the source RGB channels::

    red_out = MatrixRed[0]*r + MatrixRed[1]*g + MatrixRed[2]*b   (etc.)

Reading the same values lets the overlay follow a recoloured HUD instead of
always being stock orange.

Resolution order:

1. ``$EDSC_GRAPHICS_CONFIG`` -- explicit path to either XML file; used alone.
2. The game folder's ``GraphicsConfiguration.xml`` (base rows), with any rows
   from a ``GraphicsConfigurationOverride.xml`` found in the user's options
   directory layered on top -- the same precedence the game applies.

Missing rows fall back to the identity matrix, and ``load_matrix`` returns
``None`` when no configuration is found at all.


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
    # The journal dir sits in the same (real or Wine) user profile as the
    # options dir, so walking its parents finds nonstandard installs too.
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
    """Best-effort HUD colour matrix, or ``None`` if no config was found.

    ``journal_dir`` (the resolved journal directory, if known) anchors the
    search for the per-user override file; auto-detected locations are probed
    either way.
    """
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
