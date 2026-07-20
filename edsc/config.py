"""JSON-backed user configuration."""

# SPDX-License-Identifier: GPL-3.0-or-later

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field, fields
from pathlib import Path
from typing import Any

from . import paths

CONFIG_FILENAME = "config.json"

# Colonize radius bounds (Ly), shared by settings dialog and filter deck; the max tracks what Spansh can serve (10,000 systems/query, ~300 Ly in sparse space -- see ``systems.GRAPH_ROW_CEILING``), since a wider radius would silently return only the nearest 10,000.
COLONIZE_RANGE_MIN = 10
COLONIZE_RANGE_MAX = 300


@dataclass
class Config:
    """Persisted user settings for EDSC."""

    # Explicit journal directory override; empty means "auto-detect".
    journal_dir: str = ""

    # Overlay appearance / behaviour.
    overlay_opacity: float = 0.88
    # When enabled, use this opacity while the commander is docked.
    auto_opacity_on_dock: bool = False
    docked_opacity: float = 0.50
    # Expand the overlay at a station and collapse it when flying.
    auto_collapse_on_undock: bool = False
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
    # Station search: ask Spansh for markets updated within the last 24 hours.
    stations_recent_only: bool = False
    # Station search order: "match"=fixed best-coverage key, "nearest"=distance first, "fresh"=most recently updated market (see ``stations._sort_results``).
    stations_sort: str = "match"
    # Station search hard distance cap (Ly); 0 = unlimited (fill the page however far, the historical behaviour).
    stations_range_ly: int = 0
    # Colonize search radius (Ly) for unclaimed systems; the GUI slider spans COLONIZE_RANGE_MIN..COLONIZE_RANGE_MAX.
    colonize_range_ly: int = 20
    # Colonize ranking order: "balanced"=body-weighted score, "nearest"=pure distance, "bodies"=body count first (see ``systems.search_colonisation_targets``).
    colonize_sort: str = "balanced"
    # Colonize ranking body weight: 0=pure distance, 1=distance/body_count (historical default), higher favours body-rich systems more.
    colonize_body_weight: float = 1.0

    # Colonize result filters: refine the cached pool client-side (instant re-slice, no Spansh calls except ring types); sentinels mean "no restriction" (min_bodies 0, max_hops MAX_STEPS, min_stars 1, empty lists). See ``systems.SystemFilters``.
    colonize_min_bodies: int = 0
    colonize_max_hops: int = 10  # systems.MAX_STEPS; 10 == any reachable
    colonize_min_stars: int = 1
    colonize_terraformable_only: bool = False
    colonize_claimable_only: bool = False  # first-step systems (steps == 1)
    colonize_verified_only: bool = False   # Raven-confirmed only
    # Presence toggles vs each candidate's scanned bodies: body ELW/WW/AW/HMC/MR/GG · star scoop/WD/NS/BH.
    colonize_body_types: list = field(default_factory=list)
    colonize_star_types: list = field(default_factory=list)
    # Ring/belt composition (Metallic/Metal-rich/Rocky/Icy); unlike the others this needs the Spansh bodies endpoint, fetched lazily on first use.
    colonize_ring_types: list = field(default_factory=list)

    # Auto click-through while the game window is focused, movable again when it isn't; matchers are case-insensitive substrings of the focused window's class/title, kept specific (Proton class + exact title) so a browser tab naming "Elite Dangerous" doesn't trigger it.
    auto_click_through: bool = True
    game_window_matchers: list = field(
        default_factory=lambda: [
            "steam_app_359320",
            "elite - dangerous (client)",
            "elitedangerous64",
        ]
    )

    # Controller shortcuts apply only to the selected device; the JSON-native binding payload is parsed defensively by ``controller_bindings`` so old/hand-edited config stays safe.
    controller_device_id: str = ""
    controller_bindings: dict = field(default_factory=dict)

    # Height auto-fits the commodity list; width stays user-controlled.
    auto_height: bool = True

    # EDDN sharing: ``None``=never asked (first-run prompt owed), True/False=explicit choice; ``eddn_uploader_id`` is a random per-install UUID (never the commander name), minted when sharing is first enabled.
    eddn_enabled: bool | None = None
    eddn_uploader_id: str = ""

    # Overlay collapsed into the floating icon (▣ button / Ctrl+Shift+Down); the icon keeps its dragged position, None means "appear where the overlay is".
    collapsed: bool = False
    collapsed_x: int | None = None
    collapsed_y: int | None = None

    # Last selected project (market id) so the overlay reopens where you left off.
    selected_market_id: int | None = None

    # Flight gizmos: two frameless always-on-top windows drawing live stick input; ``flight_mapping`` is imported from Elite's bindings on first enable and ours thereafter (a game preset rewrite must not touch it), ``None`` positions mean "not placed yet".
    gizmo_enabled: bool = False
    gizmo_scale: float = 1.0
    gizmo_thrust_x: int | None = None
    gizmo_thrust_y: int | None = None
    gizmo_rotation_x: int | None = None
    gizmo_rotation_y: int | None = None
    gizmo_apply_deadzone: bool = True
    gizmo_in_flight_only: bool = True
    # Manual aim targets: global coords of the crosshair centre each gizmo leans its forward axis towards; ``None`` keeps automatic aim at screen centre.
    gizmo_thrust_target_x: int | None = None
    gizmo_thrust_target_y: int | None = None
    gizmo_rotation_target_x: int | None = None
    gizmo_rotation_target_y: int | None = None
    flight_mapping: dict = field(default_factory=dict)

    @property
    def path(self) -> Path:
        return paths.config_dir() / CONFIG_FILENAME

    @classmethod
    def _known_fields(cls) -> set[str]:
        return {f.name for f in fields(cls)}

    @classmethod
    def load(cls) -> Config:
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
        # Clamp a radius saved under an older, wider COLONIZE_RANGE_MAX; the GUI sliders clamp on display, but only once loaded, so until then the out-of-range value is live in config.
        try:
            cfg.colonize_range_ly = max(
                COLONIZE_RANGE_MIN, min(COLONIZE_RANGE_MAX, int(cfg.colonize_range_ly))
            )
        except (TypeError, ValueError):
            cfg.colonize_range_ly = cls.colonize_range_ly
        return cfg

    def save(self) -> None:
        """Atomically write config to disk."""
        directory = paths.ensure_dir(paths.config_dir())
        tmp = directory / (CONFIG_FILENAME + ".tmp")
        payload: dict[str, Any] = asdict(self)
        tmp.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        tmp.replace(directory / CONFIG_FILENAME)
