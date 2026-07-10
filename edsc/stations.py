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
    is_carrier: bool
    market_updated_at: str
    matched: list[str] = field(default_factory=list)  # needed names it stocks
    needed_total: int = 0
    covered_tons: int = 0  # sum over matched of min(supply, demand)
    demand_tons: int = 0  # sum over matched of demand (0 for amount-less lists)

    @property
    def match_count(self) -> int:
        return len(self.matched)

    @property
    def satisfaction(self) -> float:
        """Fraction (0..1) of the requested commodities this station sells."""
        if self.needed_total <= 0:
            return 0.0
        return self.match_count / self.needed_total

    @property
    def coverage(self) -> float:
        """Fraction (0..1) of the *matched* commodities' demand this station can
        actually fill (tonnage-weighted).

        1.0 means it stocks enough to cover the full outstanding demand of every
        commodity it carries (e.g. 14 of 18 items but plenty of each -> 100%); a
        lower value flags a station that lists a commodity yet is short on
        tonnage. Amount-less searches (no tons known) fall back to 1.0 whenever
        anything matched.
        """
        if self.demand_tons <= 0:
            return 1.0 if self.matched else 0.0
        return self.covered_tons / self.demand_tons




def _normalise(name: str) -> str:
    """Loose key so journal display names match Spansh commodity names."""
    return "".join(ch for ch in name.lower() if ch.isalnum())


def _is_carrier(raw: dict) -> bool:
    """Fleet carriers are identified by their station type (e.g. Drake-Class)."""
    return "carrier" in (raw.get("type", "") or "").lower()


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
    include_carriers: bool = True,
    timeout: float = 20.0,
) -> list[StationResult]:
    """Return large-pad stations near ``reference_system`` ranked by coverage.

    ``needed`` maps commodity display name -> tons still needed; a station only
    counts as stocking a commodity when its supply covers the shortfall (capped
    at a pragmatic floor). A plain list of names is also accepted and treated
    as amount-less (any positive supply counts).
    ``include_planetary`` False drops planetaryoutposts from the results.
    ``include_carriers`` False drops fleet carriers from the results.
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
        # Spansh has no carrier filter to push into the query, so drop them here.
        if not include_carriers and _is_carrier(raw):
            continue
        supply_by_key: dict[str, int] = {}
        for m in raw.get("market", []) or []:
            key = _normalise(m.get("commodity", ""))
            supply = int(m.get("supply") or 0)
            if supply > supply_by_key.get(key, 0):
                supply_by_key[key] = supply
        matched: list[str] = []
        covered_tons = 0
        demand_tons = 0
        for key, name in needed_by_key.items():
            supply = supply_by_key.get(key, 0)
            if supply < thresholds[key]:
                continue
            matched.append(name)
            demand = int(amounts.get(name) or 0)
            if demand > 0:
                demand_tons += demand
                covered_tons += min(supply, demand)
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
                is_carrier=_is_carrier(raw),
                market_updated_at=raw.get("market_updated_at", "") or "",
                matched=matched,
                needed_total=needed_total,
                covered_tons=covered_tons,
                demand_tons=demand_tons,
            )
        )

    out.sort(key=lambda s: (-s.match_count, s.distance_ly, s.arrival_ls))
    return out[:_MAX_RESULTS]


def stations_covering_missing(
    results: list[StationResult],
    needed: dict[str, int] | list[str],
) -> list[StationResult]:
    """From an existing search, pick the follow-up stations for the shortfall.

    Reuses the stations already discovered by :func:`search_stations` (no extra
    network calls). It takes the best station, works out which requested
    commodities it does *not* stock, then re-ranks the remaining stations by how
    much of that shortfall they cover. The top station plus these follow-ups form
    two complementary sets that together cover the request wherever it's stocked.
    """
    if not results:
        return []
    wanted = list(needed) if isinstance(needed, dict) else list(needed)
    top = results[0]
    matched_top = set(top.matched)
    missing = {n for n in wanted if n and n.strip() and n not in matched_top}
    if not missing:
        return []

    def covered(station: StationResult) -> int:
        return sum(1 for n in station.matched if n in missing)

    follow_up = [s for s in results[1:] if covered(s) > 0]
    follow_up.sort(key=lambda s: (-covered(s), s.distance_ly, s.arrival_ls))
    return follow_up


def plan_supply_stops(
    reference_system: str,
    needed: dict[str, int] | list[str],
    *,
    min_supply: int = 1,
    include_planetary: bool = True,
    include_carriers: bool = True,
    timeout: float = 20.0,
) -> list[StationResult]:
    """Best station plus the follow-up stops that cover the rest of ``needed``.

    Runs the initial search once and reuses those results to pick follow-up
    stations locally (no extra network calls). A second search is issued *only*
    when the discovered stations still leave some commodities uncovered - it is
    then scoped to just those leftovers, so it's the cheapest possible top-up.
    Returns the top station first, followed by the complementary stops.
    """
    results = search_stations(
        reference_system,
        needed,
        min_supply=min_supply,
        include_planetary=include_planetary,
        include_carriers=include_carriers,
        timeout=timeout,
    )
    if not results:
        return []

    top = results[0]
    follow_up = stations_covering_missing(results, needed)
    plan = [top, *follow_up]

    wanted = [n for n in (list(needed) if isinstance(needed, dict) else list(needed)) if n and n.strip()]
    covered = {n for s in plan for n in s.matched}
    leftovers = {n: (needed[n] if isinstance(needed, dict) else 0) for n in wanted if n not in covered}
    if not leftovers:
        return plan

    # Nothing found so far stocks these; widen the net for just the leftovers.
    extra = search_stations(
        reference_system,
        leftovers,
        min_supply=min_supply,
        include_planetary=include_planetary,
        include_carriers=include_carriers,
        timeout=timeout,
    )
    seen = {(s.name, s.system) for s in plan}
    plan.extend(s for s in extra if (s.name, s.system) not in seen)
    return plan

