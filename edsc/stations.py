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
from datetime import datetime, timedelta, timezone

API_URL = "https://spansh.co.uk/api/stations/search"

# Spansh combines market filters with AND. Every needed commodity therefore
# gets its own discovery query so none can silently fall outside a cap. The
# additional one-stop query is capped because it is only an optimisation: the
# per-commodity queries already provide complete discovery.
MAX_COMMODITIES = 40
_MAX_WORKERS = 8
RESULTS_PER_CATEGORY = 10
_PER_COMMODITY_SIZE = RESULTS_PER_CATEGORY
_HUGE = "10000000"  # upper bound for the supply range filter
_MIN_USEFUL_SUPPLY = 100
_RECENT_HOURS = 24

# Explicit category filters prevent a carrier-heavy result page from consuming
# all ten slots before an orbital or surface port is seen. These are the values
# currently published by Spansh's ``stations/field_values/type`` endpoint.
_ORBITAL_TYPES = [
    "Asteroid base",
    "Coriolis Starport",
    "Dodec Starport",
    "Mega ship",
    "Ocellus Starport",
    "Orbis Starport",
    "Outpost",
    "Space Construction Depot",
]
_PLANETARY_TYPES = [
    "Dockable Planet Station",
    "Planetary Construction Depot",
    "Planetary Outpost",
    "Planetary Port",
    "Settlement",
    "Surface Settlement",
]
_CARRIER_TYPES = ["Drake-Class Carrier"]
_NON_CARRIER_TYPES = [*_ORBITAL_TYPES, *_PLANETARY_TYPES]
_CATEGORY_TYPES = (_ORBITAL_TYPES, _PLANETARY_TYPES, _CARRIER_TYPES)

# Surface station types. Spansh's is_planetary flag is False for some of
# these (data quirk), so the planets toggle checks the type as well.
_PLANETARY_TYPE_SET = set(_PLANETARY_TYPES)


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
    # Who runs the place: the controlling minor faction for stations/ports,
    # the owner-given vanity name for carriers ("" when Spansh doesn't know).
    owner: str = ""
    matched: list[str] = field(default_factory=list)  # needed names it stocks
    missing: list[str] = field(default_factory=list)  # needed names it lacks
    needed_total: int = 0
    covered_tons: int = 0  # sum over matched of min(supply, demand)
    demand_tons: int = 0  # sum over matched of demand (0 for amount-less lists)
    # Needed name -> units this station stocks (any positive supply, even
    # below the match threshold: partial stock still reduces the residual).
    supply_by_name: dict[str, int] = field(default_factory=dict)
    # Needed name -> units requested (0 when the list was amount-less).
    demand_by_name: dict[str, int] = field(default_factory=dict)

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


def _is_planetary(raw: dict) -> bool:
    """Surface station, by flag or by type (the flag alone misses some)."""
    return bool(raw.get("is_planetary")) or (
        raw.get("type") or ""
    ) in _PLANETARY_TYPE_SET


def _owner(raw: dict) -> str:
    """Proprietor line for a result: stations report their controlling minor
    faction; carriers get their owner-given vanity name (Spansh publishes no
    owner beyond that, and its controlling_minor_faction is the literal
    placeholder "FleetCarrier").
    """
    if _is_carrier(raw):
        return raw.get("carrier_name", "") or ""
    return raw.get("controlling_minor_faction", "") or ""


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


def _market_filter(commodity: str, min_supply: int) -> dict:
    return {
        "name": commodity,
        "supply": {"value": [str(max(1, min_supply)), _HUGE], "comparison": "<=>"},
    }


def _recent_market_range(now: datetime | None = None) -> tuple[str, str]:
    """ISO range accepted by Spansh for markets updated in the last 24 hours."""
    end = now or datetime.now(timezone.utc)
    if end.tzinfo is None:
        end = end.replace(tzinfo=timezone.utc)
    end = end.astimezone(timezone.utc)
    start = end - timedelta(hours=_RECENT_HOURS)
    return start.isoformat(timespec="seconds"), end.isoformat(timespec="seconds")


def _query_stations(
    market_filters: list[dict],
    reference_system: str,
    station_types: list[str],
    recent_market_range: tuple[str, str] | None,
    timeout: float,
) -> list[dict]:
    """Fetch one category's nearest large-pad commodity stockists.

    Distance-sorted with no range limit: if nothing nearby qualifies, the page
    fills with whatever is out there, however far.
    """
    filters: dict = {
        "market": market_filters,
        "has_large_pad": {"value": True},
        "type": {"value": station_types},
    }
    if recent_market_range is not None:
        filters["market_updated_at"] = {
            "value": list(recent_market_range),
            "comparison": "<=>",
        }
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
    recent_only: bool = False,
    timeout: float = 20.0,
) -> list[StationResult]:
    """Fetch and rank a reusable pool of large-pad commodity stockists.

    ``needed`` maps commodity display name -> tons still needed; a station only
    counts as stocking a commodity when its supply covers the shortfall (capped
    at a pragmatic floor). A plain list of names is also accepted and treated
    as amount-less (any positive supply counts).
    Up to :data:`RESULTS_PER_CATEGORY` orbital, planetary, and carrier results
    are returned. ``include_planetary`` and ``include_carriers`` only filter that
    fetched pool for compatibility; the GUI always fetches all categories once
    and applies those choices locally. ``recent_only`` is pushed into every API
    request and accepts only markets updated in the preceding 24 hours.

    Every commodity is queried in every category, so a commodity absent from
    orbital markets can still be discovered at a surface port or carrier. A
    capped combined query per category additionally surfaces distant one-stop
    stations; it is not required for completeness.
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
    # Keep the largest shortfalls first for deterministic request order. Every
    # commodity gets an individual query; only the optional AND query is capped.
    query_names = sorted(wanted, key=lambda n: -(amounts.get(n) or 0))

    # Discover candidates in three separately paged categories. Without these
    # type filters one carrier-heavy page can hide every orbital or surface port.
    stations: dict[str, dict] = {}  # market_id/id -> raw station dict
    errors: list[str] = []
    single_filters = [
        [_market_filter(name, thresholds[_normalise(name)])] for name in query_names
    ]
    query_filters = list(single_filters)
    combined_names = query_names[:MAX_COMMODITIES]
    if len(combined_names) > 1:
        query_filters.append(
            [
                _market_filter(name, thresholds[_normalise(name)])
                for name in combined_names
            ]
        )
    recent_range = _recent_market_range() if recent_only else None
    requests = [
        (market_filters, station_types)
        for station_types in _CATEGORY_TYPES
        for market_filters in query_filters
    ]
    workers = min(_MAX_WORKERS, len(requests))
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = [
            pool.submit(
                _query_stations,
                market_filters,
                reference_system,
                station_types,
                recent_range,
                timeout,
            )
            for market_filters, station_types in requests
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
    demand_all = {n: int(amounts.get(n) or 0) for n in wanted}
    out: list[StationResult] = []
    for raw in stations.values():
        if not include_planetary and _is_planetary(raw):
            continue
        if not include_carriers and _is_carrier(raw):
            continue
        supply_by_key: dict[str, int] = {}
        for m in raw.get("market", []) or []:
            key = _normalise(m.get("commodity", ""))
            supply = int(m.get("supply") or 0)
            if supply > supply_by_key.get(key, 0):
                supply_by_key[key] = supply
        matched: list[str] = []
        missing: list[str] = []
        covered_tons = 0
        demand_tons = 0
        supply_by_name: dict[str, int] = {}
        for key, name in needed_by_key.items():
            supply = supply_by_key.get(key, 0)
            if supply > 0:
                supply_by_name[name] = supply
            if supply < thresholds[key]:
                missing.append(name)
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
                is_planetary=_is_planetary(raw),
                station_type=raw.get("type", "") or "",
                is_carrier=_is_carrier(raw),
                market_updated_at=raw.get("market_updated_at", "") or "",
                owner=_owner(raw),
                matched=matched,
                missing=missing,
                needed_total=needed_total,
                covered_tons=covered_tons,
                demand_tons=demand_tons,
                supply_by_name=supply_by_name,
                demand_by_name=dict(demand_all),
            )
        )

    out.sort(key=_station_rank)
    return _limit_categories(out)


def _station_rank(station: StationResult) -> tuple:
    return (-station.match_count, -station.coverage, station.distance_ly, station.arrival_ls)


def _station_category(station: StationResult) -> str:
    if station.is_carrier:
        return "carrier"
    if station.is_planetary:
        return "planetary"
    return "orbital"


def _limit_categories(
    results: list[StationResult],
    limit: int = RESULTS_PER_CATEGORY,
) -> list[StationResult]:
    """Keep each result category independently capped, preserving rank order."""
    counts = {"orbital": 0, "planetary": 0, "carrier": 0}
    limited: list[StationResult] = []
    for station in results:
        category = _station_category(station)
        if counts[category] >= limit:
            continue
        counts[category] += 1
        limited.append(station)
    return limited


def filter_stations(
    results: list[StationResult],
    *,
    include_planetary: bool,
    include_carriers: bool,
) -> list[StationResult]:
    """Return orbitals plus either enabled category from a fetched pool.

    These are additive include controls, never exclusive category selectors:
    orbital stations are present in all four toggle combinations.
    """
    def included(station: StationResult) -> bool:
        category = _station_category(station)
        return (
            category == "orbital"
            or (category == "planetary" and include_planetary)
            or (category == "carrier" and include_carriers)
        )

    return [station for station in results if included(station)]


def limit_mixed_results(
    results: list[StationResult],
    limit: int = RESULTS_PER_CATEGORY,
) -> list[StationResult]:
    """Cap a ranked list while retaining every category it contains.

    The first result of each available category is reserved, then the remaining
    slots are filled in the original rank order. This keeps the best result at
    the top while making an enabled category genuinely visible instead of
    letting ten results from another category crowd it out.
    """
    if len(results) <= limit:
        return list(results)
    if limit <= 0:
        return []

    representatives: dict[str, int] = {}
    for index, station in enumerate(results):
        representatives.setdefault(_station_category(station), index)

    selected = set(list(representatives.values())[:limit])
    for index in range(len(results)):
        if len(selected) >= limit:
            break
        selected.add(index)
    return [results[index] for index in sorted(selected)]


def residual_demand(
    needed: dict[str, int] | list[str],
    station: StationResult,
) -> dict[str, int]:
    """Demand left over after buying everything ``station`` can supply.

    Maps commodity -> tons still needed once the station's stock (however far
    below the match threshold) is exhausted. This is what a supplementary
    search should look for: a station can list every commodity yet cover only
    a fraction of the demanded tonnage. Amount-less entries stay at 0 tons and
    are residual only when the station doesn't stock them at all.
    """
    amounts = dict(needed) if isinstance(needed, dict) else {n: 0 for n in needed}
    out: dict[str, int] = {}
    for name, amount in amounts.items():
        if not name or not name.strip():
            continue
        supply = station.supply_by_name.get(name, 0)
        if amount > 0:
            left = amount - supply
            if left > 0:
                out[name] = left
        elif supply <= 0:
            out[name] = 0
    return out


def stations_covering_missing(
    results: list[StationResult],
    needed: dict[str, int] | list[str],
) -> list[StationResult]:
    """Pick complementary stops from an existing result pool, with no I/O."""
    if not results:
        return []
    return supplementary_stations(results, needed, results[0])


def remaining_demand(
    needed: dict[str, int] | list[str],
    stops: list[StationResult],
) -> dict[str, int]:
    """Demand still uncovered after buying from each stop once, in order."""
    remaining = dict(needed) if isinstance(needed, dict) else {n: 0 for n in needed}
    for station in stops:
        remaining = residual_demand(remaining, station)
        if not remaining:
            break
    return remaining


def supplementary_candidates(
    results: list[StationResult],
    needed: dict[str, int] | list[str],
    primary: StationResult,
) -> list[StationResult]:
    """Rank every cached alternative that contributes to the primary's gap.

    Unlike :func:`supplementary_stations`, this does not consume the residual
    after selecting a station. It is intended for an alternatives table, where
    a planetary station satisfying the gap must not hide carriers that satisfy
    it too.
    """
    remaining = residual_demand(needed, primary)
    if not remaining:
        return []
    primary_key = (primary.name, primary.system)

    def contribution(station: StationResult) -> tuple[int, int, int]:
        lines = complete = tons = 0
        for name, amount in remaining.items():
            supply = station.supply_by_name.get(name, 0)
            if supply <= 0:
                continue
            lines += 1
            if amount <= 0 or supply >= amount:
                complete += 1
            if amount > 0:
                tons += min(supply, amount)
        return lines, complete, tons

    candidates = [
        station
        for station in results
        if (station.name, station.system) != primary_key
        and contribution(station)[0] > 0
    ]
    candidates.sort(
        key=lambda station: (
            *(-value for value in contribution(station)),
            station.distance_ly,
            station.arrival_ls,
        )
    )
    return candidates


def supplementary_stations(
    results: list[StationResult],
    needed: dict[str, int] | list[str],
    primary: StationResult,
    *,
    limit: int = RESULTS_PER_CATEGORY,
) -> list[StationResult]:
    """Greedily cover ``primary``'s residual from cached candidates.

    Each iteration chooses the station covering the most remaining commodity
    lines, then the most complete lines and tonnage, before distance. Demand is
    reduced after every choice, so the returned list is a ready-to-use stop plan
    rather than ten unrelated alternatives.
    """
    remaining = residual_demand(needed, primary)
    if not remaining:
        return []

    primary_key = (primary.name, primary.system)
    candidates = [
        station
        for station in results
        if (station.name, station.system) != primary_key
    ]
    selected: list[StationResult] = []

    def contribution(station: StationResult) -> tuple[int, int, int]:
        lines = complete = tons = 0
        for name, amount in remaining.items():
            supply = station.supply_by_name.get(name, 0)
            if supply <= 0:
                continue
            lines += 1
            if amount <= 0 or supply >= amount:
                complete += 1
            if amount > 0:
                tons += min(supply, amount)
        return lines, complete, tons

    while remaining and candidates and len(selected) < limit:
        candidates.sort(
            key=lambda station: (
                *(-value for value in contribution(station)),
                station.distance_ly,
                station.arrival_ls,
            )
        )
        best = candidates.pop(0)
        if contribution(best)[0] == 0:
            break
        selected.append(best)
        remaining = residual_demand(remaining, best)

    return selected


def plan_supply_stops(
    reference_system: str,
    needed: dict[str, int] | list[str],
    *,
    min_supply: int = 1,
    include_planetary: bool = True,
    include_carriers: bool = True,
    timeout: float = 20.0,
) -> list[StationResult]:
    """Best station plus cached complementary stops, using one search only."""
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
    return [results[0], *stations_covering_missing(results, needed)]

