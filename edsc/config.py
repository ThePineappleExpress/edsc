"""User configuration, persisted as JSON in the platform config directory.


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

import json
from dataclasses import asdict, dataclass, field, fields
from pathlib import Path
from typing import Any

from . import paths

CONFIG_FILENAME = "config.json"


@dataclass
class Config:
    """Persisted user settings for EDSC."""

    # Explicit journal directory override; empty means "auto-detect".
    journal_dir: str = ""

    # Overlay appearance / behaviour.
    overlay_opacity: float = 0.88
    overlay_x: int = 60
    overlay_y: int = 60
    overlay_width: int = 380
    overlay_height: int = 460
    font_point_size: int = 10
    always_on_top: bool = True
    hide_completed: bool = False
    # Station search: include planetary (surface) stations in the results.
    stations_include_planets: bool = True
    # Station search: include fleet carriers in the results.
    stations_include_carriers: bool = True

    # Automatically ignore the mouse (click-through) while the game window is
    # focused, and become movable again when it isn't. Matchers are substrings
    # tested against the focused window's class/title (case-insensitive).
    # Defaults are deliberately specific -- the Proton window class and the
    # game's exact title -- so a browser tab mentioning "Elite Dangerous"
    # doesn't turn the overlay click-through.
    auto_click_through: bool = True
    game_window_matchers: list = field(
        default_factory=lambda: [
            "steam_app_359320",
            "elite - dangerous (client)",
            "elitedangerous64",
        ]
    )

    # Height auto-fits the commodity list; width stays user-controlled.
    auto_height: bool = True

    # Last selected project (market id) so the overlay reopens where you left off.
    selected_market_id: int | None = None

    @property
    def path(self) -> Path:
        return paths.config_dir() / CONFIG_FILENAME

    @classmethod
    def _known_fields(cls) -> set[str]:
        return {f.name for f in fields(cls)}

    @classmethod
    def load(cls) -> "Config":
        """Load config, tolerating missing files and unknown/extra keys."""
        cfg = cls()
        path = paths.config_dir() / CONFIG_FILENAME
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return cfg
        if not isinstance(data, dict):
            return cfg
        known = cls._known_fields()
        for key, value in data.items():
            if key in known:
                setattr(cfg, key, value)
        return cfg

    def save(self) -> None:
        """Atomically write config to disk."""
        directory = paths.ensure_dir(paths.config_dir())
        tmp = directory / (CONFIG_FILENAME + ".tmp")
        payload: dict[str, Any] = asdict(self)
        tmp.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        tmp.replace(directory / CONFIG_FILENAME)
