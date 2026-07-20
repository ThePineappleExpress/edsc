"""Commodity name normalization and display-name registry."""

# SPDX-License-Identifier: GPL-3.0-or-later

from __future__ import annotations

import re

_TOKEN_RE = re.compile(r"^\$(?P<body>.+?)_name;$")

# canonical id -> best known display name, learned from Name_Localised fields.
_DISPLAY_REGISTRY: dict[str, str] = {}


def canonical_name(raw: str | None) -> str:
    """Reduce any ED commodity spelling to a canonical lowercase id (``$Aluminium_name;`` -> ``aluminium``, ``aluminium`` -> ``aluminium``); unknown/empty input yields ``""``."""
    if not raw:
        return ""
    s = raw.strip()
    m = _TOKEN_RE.match(s)
    if m:
        s = m.group("body")
    return s.lower()


def register_display_name(raw: str | None, localised: str | None) -> str:
    """Record a display name for a commodity and return its canonical id."""
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
    """The learned id -> display-name mapping, for persistence; display names are only learned from live journal events, so persisting them keeps names (and the Spansh queries built from them) correct even when those journals are gone."""
    return dict(_DISPLAY_REGISTRY)


def restore_registry(mapping: dict[str, str] | None) -> None:
    """Re-learn persisted display names. Live events still take precedence."""
    for key, name in (mapping or {}).items():
        if key and name and key not in _DISPLAY_REGISTRY:
            _DISPLAY_REGISTRY[str(key)] = str(name)
