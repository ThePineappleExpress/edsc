"""Pure, I/O-free planning over station search results; owns the :class:`StationResult` domain model and every operation over a *fetched* pool (ranking, category caps, include-filters, residual/supplementary stop planning), with no network access -- the Spansh client lives in :mod:`edsc.stations`, which re-exports these names so the ``edsc.stations.*`` surface is unchanged."""

# SPDX-License-Identifier: GPL-3.0-or-later

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

# How many results each station category (orbital / planetary / carrier) contributes, and the default cap the planning helpers reason about.
RESULTS_PER_CATEGORY = 10

StationCategory = Literal["orbital", "planetary", "carrier"]


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
    owner: str = ""
    matched: list[str] = field(default_factory=list)
    missing: list[str] = field(default_factory=list)
    needed_total: int = 0
    covered_tons: int = 0
    demand_tons: int = 0
    supply_by_name: dict[str, int] = field(default_factory=dict)
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
        """Tonnage-weighted coverage of matched demand, from zero to one."""
        if self.demand_tons <= 0:
            return 1.0 if self.matched else 0.0
        return self.covered_tons / self.demand_tons


def _amounts(needed: dict[str, int] | list[str]) -> dict[str, int]:
    return dict(needed) if isinstance(needed, dict) else dict.fromkeys(needed, 0)


def _station_rank(station: StationResult) -> tuple:
    return (-station.match_count, -station.coverage, station.distance_ly, station.arrival_ls)


def _sort_results(out: list[StationResult], sort: str) -> None:
    """Order the pool in place by the chosen strategy: ``match`` (default) keeps the fixed best-coverage key, ``nearest`` leads with distance, ``fresh`` with the most recently updated market; both alternatives fall back on best-coverage as a stable tie-breaker."""
    if sort == "nearest":
        out.sort(
            key=lambda s: (
                s.distance_ly,
                s.arrival_ls,
                -s.match_count,
                -s.coverage,
            )
        )
    elif sort == "fresh":
        out.sort(key=_station_rank)
        out.sort(key=lambda s: s.market_updated_at or "", reverse=True)
    else:
        out.sort(key=_station_rank)


def station_category(station: StationResult) -> StationCategory:
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
        category = station_category(station)
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
    """Return orbitals plus either enabled category from a fetched pool; these are additive include controls, not exclusive category selectors -- orbital stations are present in all four toggle combinations."""
    categories = {"orbital"}
    if include_planetary:
        categories.add("planetary")
    if include_carriers:
        categories.add("carrier")
    return [
        station for station in results if station_category(station) in categories
    ]


def limit_mixed_results(
    results: list[StationResult],
    limit: int = RESULTS_PER_CATEGORY,
) -> list[StationResult]:
    """Cap a ranked list while retaining every category it contains: the first result of each available category is reserved, then remaining slots fill in rank order -- keeping the best at the top while making an enabled category visible instead of letting ten results from another crowd it out."""
    if len(results) <= limit:
        return list(results)
    if limit <= 0:
        return []

    representatives: dict[str, int] = {}
    for index, station in enumerate(results):
        representatives.setdefault(station_category(station), index)

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
    """Demand left after buying everything ``station`` can supply: commodity -> tons still needed once its stock (however far below the match threshold) is exhausted -- what a supplementary search should look for, since a station can list every commodity yet cover only a fraction of the tonnage; amount-less entries stay at 0 tons, residual only when not stocked at all."""
    amounts = _amounts(needed)
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
    remaining = _amounts(needed)
    for station in stops:
        remaining = residual_demand(remaining, station)
        if not remaining:
            break
    return remaining


def _contribution(
    station: StationResult,
    needed: dict[str, int],
) -> tuple[int, int, int]:
    lines = complete = tons = 0
    for name, amount in needed.items():
        supply = station.supply_by_name.get(name, 0)
        if supply <= 0:
            continue
        lines += 1
        complete += amount <= 0 or supply >= amount
        if amount > 0:
            tons += min(supply, amount)
    return lines, complete, tons


def supplementary_candidates(
    results: list[StationResult],
    needed: dict[str, int] | list[str],
    primary: StationResult,
) -> list[StationResult]:
    """Rank every cached alternative that contributes to the primary's gap; unlike :func:`supplementary_stations` it doesn't consume the residual after selecting a station, being for an alternatives table where a planetary station satisfying the gap must not hide carriers that satisfy it too."""
    remaining = residual_demand(needed, primary)
    if not remaining:
        return []
    primary_key = (primary.name, primary.system)

    candidates: list[tuple[StationResult, tuple[int, int, int]]] = []
    for station in results:
        if (station.name, station.system) == primary_key:
            continue
        score = _contribution(station, remaining)
        if score[0]:
            candidates.append((station, score))
    candidates.sort(
        key=lambda item: (
            *(-value for value in item[1]),
            item[0].distance_ly,
            item[0].arrival_ls,
        )
    )
    return [station for station, _score in candidates]


def supplementary_stations(
    results: list[StationResult],
    needed: dict[str, int] | list[str],
    primary: StationResult,
    *,
    limit: int = RESULTS_PER_CATEGORY,
) -> list[StationResult]:
    """Greedily cover ``primary``'s residual from cached candidates: each iteration picks the station covering the most remaining commodity lines (then most complete lines/tonnage, then distance) and reduces demand after every choice, so the result is a ready-to-use stop plan rather than ten unrelated alternatives."""
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

    while remaining and candidates and len(selected) < limit:
        best = max(
            candidates,
            key=lambda station: (
                *_contribution(station, remaining),
                -station.distance_ly,
                -station.arrival_ls,
            ),
        )
        if _contribution(best, remaining)[0] == 0:
            break
        candidates.remove(best)
        selected.append(best)
        remaining = residual_demand(remaining, best)

    return selected
