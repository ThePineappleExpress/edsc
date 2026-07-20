"""Find and rank Spansh stations that stock requested commodities."""

# SPDX-License-Identifier: GPL-3.0-or-later

from __future__ import annotations

import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta

from . import spansh
from .station_planning import (
    RESULTS_PER_CATEGORY,
    StationCategory as StationCategory,
    StationResult,
    _amounts,
    _limit_categories,
    _sort_results,
    filter_stations as filter_stations,
    limit_mixed_results as limit_mixed_results,
    remaining_demand as remaining_demand,
    residual_demand as residual_demand,
    station_category as station_category,
    stations_covering_missing as stations_covering_missing,
    supplementary_candidates as supplementary_candidates,
    supplementary_stations as supplementary_stations,
)

API_URL = spansh.STATIONS_URL

# Spansh ANDs market filters, so every needed commodity gets its own discovery query so none can silently fall outside a cap; the extra one-stop query is capped since it's only an optimisation (the per-commodity queries already give complete discovery).
MAX_COMMODITIES = 40
_MAX_WORKERS = 8
_PER_COMMODITY_SIZE = RESULTS_PER_CATEGORY
_HUGE = "10000000"  # upper bound for the supply range filter
_MIN_USEFUL_SUPPLY = 100
_RECENT_HOURS = 24

# Explicit category filters prevent a carrier-heavy page consuming all ten slots before an orbital/surface port is seen; these are the values Spansh's ``stations/field_values/type`` endpoint currently publishes.
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
# Public: the non-carrier station types, shared with ``systems`` for its colonisation-agent lookups.
NON_CARRIER_TYPES = [*_ORBITAL_TYPES, *_PLANETARY_TYPES]
_CATEGORY_TYPES = (_ORBITAL_TYPES, _PLANETARY_TYPES, _CARRIER_TYPES)

# Surface station types; Spansh's is_planetary flag is False for some of these (data quirk), so the planets toggle checks the type too.
_PLANETARY_TYPE_SET = set(_PLANETARY_TYPES)


class StationSearchError(RuntimeError):
    """Raised when the Spansh search cannot be completed."""


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
    """Return a station's faction or a carrier's community-sourced name."""
    if _is_carrier(raw):
        return raw.get("carrier_name", "") or ""
    return raw.get("controlling_minor_faction", "") or ""


def _post(body: dict, timeout: float) -> dict:
    return spansh.post(API_URL, body, timeout)


def _market_filter(commodity: str, min_supply: int) -> dict:
    return {
        "name": commodity,
        "supply": {"value": [str(max(1, min_supply)), _HUGE], "comparison": "<=>"},
    }


def _recent_market_range(now: datetime | None = None) -> tuple[str, str]:
    return spansh.utc_range(timedelta(hours=_RECENT_HOURS), now)


def _query_stations(
    market_filters: list[dict],
    reference_system: str,
    station_types: list[str],
    recent_market_range: tuple[str, str] | None,
    timeout: float,
    range_ly: int = 0,
) -> list[dict]:
    """Fetch one category's nearest large-pad commodity stockists, distance-sorted; with ``range_ly <= 0`` there's no range limit (the page fills with whatever's out there, however far), a positive ``range_ly`` caps the radius."""
    filters: dict = {
        "market": market_filters,
        "has_large_pad": {"value": True},
        "type": {"value": station_types},
    }
    if range_ly > 0:
        filters["distance"] = {"min": "0", "max": str(range_ly)}
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
    range_ly: int = 0,
    sort: str = "match",
    timeout: float = 20.0,
) -> list[StationResult]:
    """Fetch and rank a reusable pool of large-pad commodity stockists; ``needed`` maps commodity display name -> tons still needed (a station stocks a commodity only when its supply covers the shortfall, capped at a floor), a plain name list treated as amount-less. Up to :data:`RESULTS_PER_CATEGORY` orbital/planetary/carrier results are returned; ``include_planetary``/``include_carriers`` only filter the fetched pool (the GUI fetches all categories once and applies choices locally), and ``recent_only`` is pushed into every request (markets updated within 24h). Every commodity is queried in every category, so one absent from orbital markets can still surface at a surface port or carrier; the capped combined query per category surfaces distant one-stop stations but isn't required for completeness."""
    reference_system = (reference_system or "").strip()
    if not reference_system:
        raise StationSearchError("Current star system is unknown yet.")
    amounts = _amounts(needed)
    wanted = [n for n in amounts if n and n.strip()]
    if not wanted:
        return []
    needed_total = len(wanted)
    needed_by_key = {_normalise(n): n for n in wanted}
    thresholds = {
        _normalise(n): _supply_threshold(int(amounts.get(n) or 0), min_supply)
        for n in wanted
    }
    # Keep the largest shortfalls first for deterministic request order; every commodity gets an individual query, only the optional AND query is capped.
    query_names = sorted(wanted, key=lambda n: -(amounts.get(n) or 0))

    # Discover candidates in three separately paged categories; without these type filters one carrier-heavy page can hide every orbital or surface port.
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
                range_ly,
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

    # Score each discovered station against the *full* needed list using its own market array (authoritative), not just the commodity that surfaced it.
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

    _sort_results(out, sort)
    return _limit_categories(out)

