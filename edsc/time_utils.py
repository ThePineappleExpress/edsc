"""UTC date and time helpers."""

# SPDX-License-Identifier: GPL-3.0-or-later

from __future__ import annotations

from datetime import datetime, timezone


def parse_timestamp(value: object) -> datetime | None:
    """Parse an ISO-8601 timestamp and normalize it to UTC."""
    if not isinstance(value, str) or not (text := value.strip()):
        return None
    if text.endswith("Z"):
        text = f"{text[:-1]}+00:00"
    elif (
        ("T" in text or " " in text)
        and text[-3:-2] in ("+", "-")
        and text[-2:].isdigit()
    ):
        text = f"{text}:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)