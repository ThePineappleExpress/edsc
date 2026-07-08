"""Spansh station-search client: find the nearest stations selling what you need.


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
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field

API_URL = "https://spansh.co.uk/api/stations/search"

# Guard rail so a huge colonisation list can't spawn hundreds of requests.
# Only the *queries* are capped; stations are still scored against
# the full needed list using their own market arrays.
MAX_COMMODITIES = 40
_MAX_WORKERS = 8
_PER_COMMODITY_SIZE = 25  # nearest stations fetched per commodity query
_MAX_RESULTS = 25  # ranked stations returned to the caller
_HUGE = "10000000"  # upper bound for the supply range filter
_MIN_USEFUL_SUPPLY = 100


class StationSearchError(RuntimeError):
    """Raised when the Spansh search cannot be completed."""


@dataclass
class StationResult:
    """One station and how well it covers the requested commodity list."""

    name: str
    system: str
    distance_ly: float
    arrival_ls: float
    has_large_pad: bool
    is_planetary: bool
    station_type: str
    market_updated_at: str
    matched: list[str] = field(default_factory=list)  # needed names it stocks
    needed_total: int = 0

    @property
    def match_count(self) -> int:
        return len(self.matched)

    @property
    def satisfaction(self) -> float:
        """Fraction (0..1) of the requested commodities this station sells."""
        if self.needed_total <= 0:
            return 0.0
        return self.match_count / self.needed_total


def _normalise(name: str) -> str:
    """Loose key so journal display names match Spansh commodity names."""
    return "".join(ch for ch in name.lower() if ch.isalnum())


def _post(body: dict, timeout: float) -> dict:
    data = json.dumps(body).encode("utf-8")
    req = urllib.request.Request(
        API_URL,
        data=data,
        headers={"Content-Type": "application/json", "Accept": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _query_commodity(
    commodity: str,
    reference_system: str,
    min_supply: int,
    include_planetary: bool,
    timeout: float,
) -> list[dict]:
    """Fetch the nearest large-pad stations that currently stock one commodity.
    """
    market_filter = {
        "name": commodity,
        "supply": {"value": [str(max(1, min_supply)), _HUGE], "comparison": "<=>"},
    }
    filters: dict = {
        "market": [market_filter],
        "has_large_pad": {"value": True},
    }
    if not include_planetary:
        filters["is_planetary"] = {"value": False}
    body = {
        "filters": filters,
        "sort": [{"distance": {"direction": "asc"}}],
        "size": _PER_COMMODITY_SIZE,
        "page": 0,
        "reference_system": reference_system,
    }
    payload = _post(body, timeout)
    return payload.get("results", []) or []


def _supply_threshold(amount: int, min_supply: int) -> int:
    """Units a station must stock for a commodity to count as available."""
    if amount <= 0:
        return max(1, min_supply)
    return max(1, min_supply, min(amount, _MIN_USEFUL_SUPPLY))


def search_stations(
    reference_system: str,
    needed: dict[str, int] | list[str],
    *,
    min_supply: int = 1,
    include_planetary: bool = True,
    timeout: float = 20.0,
) -> list[StationResult]:
    """Return large-pad stations near ``reference_system`` ranked by coverage.

    ``needed`` maps commodity display name -> tons still needed; a station only
    counts as stocking a commodity when its supply covers the shortfall (capped
    at a pragmatic floor). A plain list of names is also accepted and treated
    as amount-less (any positive supply counts). 
    ``include_planetary`` False drops planetaryoutposts from the results.
    """
    reference_system = (reference_system or "").strip()
    if not reference_system:
        raise StationSearchError("Current star system is unknown yet.")
    amounts = dict(needed) if isinstance(needed, dict) else {n: 0 for n in needed}
    wanted = [n for n in amounts if n and n.strip()]
    if not wanted:
        return []
    needed_total = len(wanted)
    needed_by_key = {_normalise(n): n for n in wanted}
    thresholds = {
        _normalise(n): _supply_threshold(int(amounts.get(n) or 0), min_supply)
        for n in wanted
    }
    # Query the biggest shortfalls first so the request cap trims the least
    # pressing commodities; scoring below still covers the full list.
    query_names = sorted(wanted, key=lambda n: -(amounts.get(n) or 0))
    query_names = query_names[:MAX_COMMODITIES]

    # Discover candidate stations by querying each commodity concurrently.
    stations: dict[str, dict] = {}  # market_id/id -> raw station dict
    errors: list[str] = []
    workers = min(_MAX_WORKERS, len(query_names))
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = [
            pool.submit(
                _query_commodity,
                name,
                reference_system,
                thresholds[_normalise(name)],
                include_planetary,
                timeout,
            )
            for name in query_names
        ]
        for fut in futures:
            try:
                results = fut.result()
            except urllib.error.HTTPError as exc:  # pragma: no cover - network
                errors.append(f"HTTP {exc.code}")
                continue
            except (urllib.error.URLError, TimeoutError, ValueError) as exc:
                errors.append(str(exc))
                continue
            for raw in results:
                key = str(raw.get("market_id") or raw.get("id") or raw.get("name"))
                stations.setdefault(key, raw)

    if not stations and errors:
        raise StationSearchError(f"Spansh request failed: {errors[0]}")

    # Score each discovered station against the *full* needed list using its own
    # market array (authoritative), not just the commodity that surfaced it.
    out: list[StationResult] = []
    for raw in stations.values():
        if not include_planetary and raw.get("is_planetary"):
            continue
        supply_by_key: dict[str, int] = {}
        for m in raw.get("market", []) or []:
            key = _normalise(m.get("commodity", ""))
            supply = int(m.get("supply") or 0)
            if supply > supply_by_key.get(key, 0):
                supply_by_key[key] = supply
        matched = [
            name
            for key, name in needed_by_key.items()
            if supply_by_key.get(key, 0) >= thresholds[key]
        ]
        if not matched:
            continue
        out.append(
            StationResult(
                name=raw.get("name", "") or "",
                system=raw.get("system_name", "") or "",
                distance_ly=float(raw.get("distance", 0.0) or 0.0),
                arrival_ls=float(raw.get("distance_to_arrival", 0.0) or 0.0),
                has_large_pad=bool(raw.get("has_large_pad")),
                is_planetary=bool(raw.get("is_planetary")),
                station_type=raw.get("type", "") or "",
                market_updated_at=raw.get("market_updated_at", "") or "",
                matched=matched,
                needed_total=needed_total,
            )
        )

    out.sort(key=lambda s: (-s.match_count, s.distance_ly, s.arrival_ls))
    return out[:_MAX_RESULTS]
