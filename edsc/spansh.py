"""Shared transport and time helpers for Spansh API clients."""

# SPDX-License-Identifier: GPL-3.0-or-later

from __future__ import annotations

import json
import urllib.request
from datetime import datetime, timedelta, timezone

from . import trace

STATIONS_URL = "https://spansh.co.uk/api/stations/search"
SYSTEMS_URL = "https://spansh.co.uk/api/systems/search"
BODIES_URL = "https://spansh.co.uk/api/bodies/search"


def utc_range(
    age: timedelta,
    now: datetime | None = None,
) -> tuple[str, str]:
    """Return the UTC ISO range from ``now - age`` through ``now``."""
    end = now or datetime.now(timezone.utc)
    if end.tzinfo is None:
        end = end.replace(tzinfo=timezone.utc)
    end = end.astimezone(timezone.utc)
    start = end - age
    return start.isoformat(timespec="seconds"), end.isoformat(timespec="seconds")


def post(url: str, body: dict, timeout: float) -> dict:
    data = json.dumps(body).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=data,
        headers={"Content-Type": "application/json", "Accept": "application/json"},
        method="POST",
    )
    endpoint = url.rsplit("/api/", 1)[-1]
    trace.dump(f"POST {endpoint} <-", body)
    with trace.timed(f"POST {endpoint} ->") as note:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
        results = payload.get("results") or []
        note.say(f"{len(results)} rows of count={payload.get('count')}")
        if results:
            trace.dump("  first row", results[0])
    return payload
