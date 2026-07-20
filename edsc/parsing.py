"""Coercion helpers for untrusted API payloads; Spansh and Raven Colonial both serve community data where any field may be absent, null, or the wrong type, so these turn "whatever came back" into a typed value or ``None`` and keep the parsers declarative."""

# SPDX-License-Identifier: GPL-3.0-or-later

from __future__ import annotations


def float_or_none(value: object) -> float | None:
    """``value`` as a float, or ``None`` if it isn't numeric."""
    try:
        return float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None


def int_or_none(value: object) -> int | None:
    """``value`` as an int, or ``None`` if it isn't numeric."""
    try:
        return int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
