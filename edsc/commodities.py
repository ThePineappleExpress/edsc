"""Commodity name handling.


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

import re

_TOKEN_RE = re.compile(r"^\$(?P<body>.+?)_name;$")

# canonical id -> best known display name, learned from Name_Localised fields.
_DISPLAY_REGISTRY: dict[str, str] = {}


def canonical_name(raw: str | None) -> str:
    """Reduce any ED commodity spelling to a canonical lowercase id.

    ``$Aluminium_name;`` -> ``aluminium``; ``aluminium`` -> ``aluminium``.
    Unknown/empty input yields ``""``.
    """
    if not raw:
        return ""
    s = raw.strip()
    m = _TOKEN_RE.match(s)
    if m:
        s = m.group("body")
    return s.lower()


def register_display_name(raw: str | None, localised: str | None) -> str:
    """Record a display name for a commodity and return its canonical id.
    """
    key = canonical_name(raw)
    if key and localised and key not in _DISPLAY_REGISTRY:
        _DISPLAY_REGISTRY[key] = localised
    return key


def display_name(key: str) -> str:
    """Human-readable name for a canonical id, falling back to a title-cased id."""
    if key in _DISPLAY_REGISTRY:
        return _DISPLAY_REGISTRY[key]
    # Fall back to a readable-ish rendering of the internal id.
    return key.replace("_", " ").title() if key else ""


def registry_snapshot() -> dict[str, str]:
    """The learned id -> display-name mapping, for persistence.

    Display names are only learned from live journal events; persisting them
    keeps names (and the Spansh queries built from them) correct even when the
    journals that taught us the names are gone.
    """
    return dict(_DISPLAY_REGISTRY)


def restore_registry(mapping: dict[str, str] | None) -> None:
    """Re-learn persisted display names. Live events still take precedence."""
    for key, name in (mapping or {}).items():
        if key and name and key not in _DISPLAY_REGISTRY:
            _DISPLAY_REGISTRY[str(key)] = str(name)
