"""Find and rank nearby colonizable systems through the Spansh API."""

# SPDX-License-Identifier: GPL-3.0-or-later

from __future__ import annotations

import urllib.error
import urllib.request
from collections import Counter
from collections.abc import Sequence
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass, field, replace
from datetime import datetime, timedelta

from . import raven, spansh, trace
from .parsing import float_or_none
from .stations import NON_CARRIER_TYPES

# In-game rule: a claim is bought at a colonisation contact within this many Ly of the target.
CLAIM_RANGE_LY = 16.0
MAX_STEPS = 10
COLONISATION_SERVICE = "System Colonisation"
DEFAULT_RESULTS = 30
# Confirm more candidates than the page shows so a Raven-confirmed system just outside the window can outrank a Spansh-only one; bounded since each check is one (session-cached) Raven request.
VERIFY_POOL_SIZE = 90
# How many ranked candidates a ring filter resolves ring data for; those below the bound stay unresolved and are dropped, since a ring filter can't be judged without them.
RING_LOOKUP_LIMIT = 300
SYSTEM_DATA_MAX_AGE_DAYS = 7
_AGENT_PAGE_SIZE = 20
_MAX_AGENT_WORKERS = 8
_GRAPH_PAGE_SIZE = 500
# Hard ceiling, not a tuning knob: Spansh serves at most 10,000 rows/query (past it answers HTTP 500), so a wide search covers only the nearest 10,000 systems (what ``covered_ly`` reports, and why COLONIZE_RANGE_MAX sits near it).
GRAPH_ROW_CEILING = 10_000
_MAX_GRAPH_PAGES = GRAPH_ROW_CEILING // _GRAPH_PAGE_SIZE

# Dockable types that can host a colonisation contact: depots/settlements never offer it, carriers cannot.
_AGENT_EXCLUDED_TYPES = {
    "Space Construction Depot",
    "Planetary Construction Depot",
    "Settlement",
    "Surface Settlement",
}
AGENT_STATION_TYPES = [
    t for t in NON_CARRIER_TYPES if t not in _AGENT_EXCLUDED_TYPES
]


class SystemSearchError(RuntimeError):
    """Raised when the Spansh colonization search cannot be completed."""


@dataclass
class BodyInfo:
    """One known body of a candidate system (Spansh only lists scanned ones)."""

    name: str
    type: str  # "Star" / "Planet"
    subtype: str
    distance_to_arrival: float | None
    is_main_star: bool
    terraforming_state: str


@dataclass
class AgentStation:
    """Nearest station offering the colonisation contact for a candidate."""

    name: str
    system: str
    distance_ly: float  # from the candidate system


@dataclass
class SystemResult:
    """One colonization candidate and everything the table shows about it."""

    name: str
    id64: int | None = None
    distance_ly: float | None = None  # from the player's reference system
    x: float | None = None
    y: float | None = None
    z: float | None = None
    body_count: int | None = None  # honk total; may exceed len(bodies)
    bodies: list[BodyInfo] = field(default_factory=list)
    nearest_populated_ly: float | None = None
    updated_at: str = ""
    steps: int | None = None  # colonies needed incl. this one; None = >MAX_STEPS
    agent: AgentStation | None = None
    agent_error: bool = False  # lookup failed (retry via refresh), not "none"
    # Raven Colonial cross-check: True=on record (confirmed), False=Spansh-only (hypothetical), None=unchecked/failed (don't penalise).
    verified: bool | None = None

    @property
    def star_count(self) -> int | None:
        if not self.bodies:
            return None
        return sum(1 for b in self.bodies if b.type == "Star")

    @property
    def known_body_count(self) -> int | None:
        """Honk total when known, otherwise the number of scanned bodies."""
        if self.body_count is not None:
            return self.body_count
        return len(self.bodies) or None

    @property
    def furthest_ls(self) -> float | None:
        distances = [
            b.distance_to_arrival
            for b in self.bodies
            if b.distance_to_arrival is not None
        ]
        return max(distances) if distances else None

    @property
    def terraformable_count(self) -> int:
        return sum(
            1
            for b in self.bodies
            if b.terraforming_state not in ("", "Not terraformable")
        )

    @property
    def claimable(self) -> bool:
        """Claimable right now: a contact exists within claim range."""
        return (
            self.steps == 1
            and self.agent is not None
            and self.agent.distance_ly <= CLAIM_RANGE_LY
        )

    @property
    def rank_score(self) -> float:
        """Distance weighted by body count at the default weight, lower better."""
        return self.weighted_score(1.0)

    def weighted_score(self, weight: float) -> float:
        """Distance discounted by body count (lower better); ``weight`` scales the pull: 0=pure distance, 1=``distance/body_count``, higher favours body-rich systems more."""
        if self.distance_ly is None:
            return float("inf")
        bodies = max(1, self.known_body_count or 1)
        return self.distance_ly / (1.0 + weight * (bodies - 1))


@dataclass
class ColonizeSearch:
    """Ranked page of candidates plus how complete the reachability graph was."""

    results: list[SystemResult]
    # Spansh's count of unclaimed systems in range; saturates at GRAPH_ROW_CEILING, so "at least this many", never a total.
    total_in_range: int = 0
    reachable: int = 0  # of those, reachable in <= MAX_STEPS colonization steps
    graph_truncated: bool = False  # the radius holds more systems than were fetched
    # How far the search actually reached; equals the radius asked for unless the row ceiling cut it short (a truncated search says nothing past this distance).
    covered_ly: float | None = None
    ring_error: bool = False  # the ring lookup failed (results un-ring-filtered)
    # Every reachable candidate, deliberately *not* narrowed by ``filters``, so the overlay can re-slice client-side (incl. loosening a filter) without another Spansh search.
    pool: list[SystemResult] = field(default_factory=list)


# --- Result filters -------------------------------------------------------
# Result filters run over the cached pool client-side (no Spansh call); the exception is ring composition, which the systems endpoint lacks -- resolved lazily and passed in separately (see ``fetch_ring_map``).

# body-type toggle key -> lowercase substring matched against a body's subtype.
BODY_TYPE_MATCH = {
    "ELW": "earth-like",
    "WW": "water world",
    "AW": "ammonia world",
    "HMC": "high metal content",
    "MR": "metal-rich",
    "GG": "gas giant",
}
# star-type toggle key -> lowercase substring (scoopable is handled separately).
STAR_KIND_MATCH = {
    "WD": "white dwarf",
    "NS": "neutron",
    "BH": "black hole",
}
# Spectral classes that can refuel a ship (KGBFOAM); brown dwarfs (L/T/Y) share none of these leading letters, so a class check suffices.
STAR_SCOOPABLE_CLASSES = frozenset("OBAFGKM")
# Ring composition classes Spansh reports (exact strings), for the ring toggles.
RING_TYPES = ("Metallic", "Metal Rich", "Rocky", "Icy")


@dataclass
class SystemFilters:
    """Client-side refinements on the cached pool; every default is neutral (all-default keeps the full pool) and all presence lists are AND-combined (a candidate must satisfy every entry)."""

    min_bodies: int = 0
    max_hops: int = MAX_STEPS
    min_stars: int = 1
    terraformable_only: bool = False
    claimable_only: bool = False  # first-step systems (steps == 1)
    verified_only: bool = False
    body_types: tuple[str, ...] = ()
    star_types: tuple[str, ...] = ()
    ring_types: tuple[str, ...] = ()

    @property
    def needs_ring_data(self) -> bool:
        return bool(self.ring_types)


def _primary_star(s: SystemResult) -> BodyInfo | None:
    """The system's arrival star, else any star, else None."""
    stars = [b for b in s.bodies if b.type == "Star"]
    for b in stars:
        if b.is_main_star:
            return b
    return stars[0] if stars else None


def _is_scoopable(s: SystemResult) -> bool:
    star = _primary_star(s)
    if star is None or not star.subtype:
        return False
    return (
        star.subtype[0] in STAR_SCOOPABLE_CLASSES
        and "brown dwarf" not in star.subtype.lower()
    )


def _has_body_type(s: SystemResult, key: str) -> bool:
    needle = BODY_TYPE_MATCH.get(key)
    if not needle:
        return False
    return any(needle in (b.subtype or "").lower() for b in s.bodies)


def _has_star_kind(s: SystemResult, key: str) -> bool:
    if key == "scoop":
        return _is_scoopable(s)
    needle = STAR_KIND_MATCH.get(key)
    if not needle:
        return False
    return any(
        needle in (b.subtype or "").lower() for b in s.bodies if b.type == "Star"
    )


def rejection_reason(
    s: SystemResult,
    f: SystemFilters,
    ring_types_by_system: dict[str, set[str]] | None = None,
) -> str | None:
    """Name the first filter in ``f`` that rejects ``s``, else None; the attribution lets :func:`filter_census` report which filter culls a search without duplicating these rules."""
    if (s.known_body_count or 0) < f.min_bodies:
        return "min_bodies"
    if s.steps is None or s.steps > f.max_hops:
        return "max_hops"
    # Only enforce a star count above 1: Spansh lists scanned bodies only, so an unscanned star reports 0; "Any" (1) must not drop it.
    if f.min_stars > 1 and (s.star_count or 0) < f.min_stars:
        return "min_stars"
    if f.terraformable_only and s.terraformable_count == 0:
        return "terraformable_only"
    if f.claimable_only and s.steps != 1:
        return "claimable_only"
    if f.verified_only and s.verified is not True:
        return "verified_only"
    if any(not _has_body_type(s, k) for k in f.body_types):
        return "body_types"
    if any(not _has_star_kind(s, k) for k in f.star_types):
        return "star_types"
    if f.ring_types:
        have = (ring_types_by_system or {}).get(s.name, set())
        if any(rt not in have for rt in f.ring_types):
            return "ring_types"
    return None


def passes_filters(
    s: SystemResult,
    f: SystemFilters,
    ring_types_by_system: dict[str, set[str]] | None = None,
) -> bool:
    """True when ``s`` satisfies every active filter in ``f``; ring filters need ``ring_types_by_system`` (a system absent from that map counts as having no rings)."""
    return rejection_reason(s, f, ring_types_by_system) is None


# Every refinement axis of SystemFilters, for the census below; each is neutral at its dataclass default (how "is this filter on?" is decided).
FILTER_AXES = (
    "min_bodies",
    "max_hops",
    "min_stars",
    "terraformable_only",
    "claimable_only",
    "verified_only",
    "body_types",
    "star_types",
    "ring_types",
)


def filter_census(
    pool: list[SystemResult],
    f: SystemFilters,
    ring_types_by_system: dict[str, set[str]] | None = None,
) -> dict[str, int]:
    """Active filter axis -> how many of ``pool`` it rejects *on its own*; measured in isolation to answer "which toggle emptied my list?" (a sequential tally would over-credit the first filter)."""
    neutral = SystemFilters()
    census: dict[str, int] = {}
    for axis in FILTER_AXES:
        value = getattr(f, axis)
        if value == getattr(neutral, axis):
            continue  # this filter is off; it rejects nothing
        solo = replace(neutral, **{axis: value})
        census[axis] = sum(
            1 for s in pool if not passes_filters(s, solo, ring_types_by_system)
        )
    return census


# Agent lookups are expensive (one stations query each) and stable; definitive answers (incl. "none nearby") are session-cached.
_agent_cache: dict[object, AgentStation | None] = {}


def _fresh_system_range(now: datetime | None = None) -> tuple[str, str]:
    return spansh.utc_range(timedelta(days=SYSTEM_DATA_MAX_AGE_DAYS), now)


def _query_systems_page(
    reference_system: str,
    range_ly: int,
    page: int,
    updated_range: tuple[str, str],
    timeout: float,
) -> dict:
    # No is_colonised/is_being_colonised filters: Spansh's boolean filters drop records lacking the field, and frontier systems often carry neither, emptying exactly the regions this search targets; both are checked client-side instead.
    body = {
        "filters": {
            "population": {"comparison": "<=>", "value": [0, 0]},
            "needs_permit": {"value": False},
            "distance": {"min": "0", "max": str(range_ly)},
            "updated_at": {
                "comparison": "<=>",
                "value": list(updated_range),
            },
        },
        "sort": [{"distance": {"direction": "asc"}}],
        "size": _GRAPH_PAGE_SIZE,
        "page": page,
        "reference_system": reference_system,
    }
    try:
        return spansh.post(spansh.SYSTEMS_URL, body, timeout)
    except urllib.error.HTTPError as exc:
        raise SystemSearchError(f"Spansh request failed: HTTP {exc.code}") from exc
    except (urllib.error.URLError, TimeoutError, ValueError) as exc:
        raise SystemSearchError(f"Spansh request failed: {exc}") from exc


def _parse_system(raw: dict) -> SystemResult:
    """Build a result from a raw record, tolerating any missing key."""
    bodies: list[BodyInfo] = []
    for b in raw.get("bodies") or []:
        bodies.append(
            BodyInfo(
                name=b.get("name", "") or "",
                type=b.get("type", "") or "",
                subtype=b.get("subtype", "") or "",
                distance_to_arrival=float_or_none(b.get("distance_to_arrival")),
                is_main_star=bool(b.get("is_main_star")),
                terraforming_state=b.get("terraforming_state", "") or "",
            )
        )
    body_count = raw.get("body_count")
    return SystemResult(
        name=raw.get("name", "") or "",
        id64=raw.get("id64"),
        distance_ly=float_or_none(raw.get("distance")),
        x=float_or_none(raw.get("x")),
        y=float_or_none(raw.get("y")),
        z=float_or_none(raw.get("z")),
        body_count=int(body_count) if body_count is not None else None,
        bodies=bodies,
        nearest_populated_ly=float_or_none(raw.get("nearest_populated_distance")),
        updated_at=raw.get("updated_at", "") or "",
    )


@dataclass
class _Graph:
    """What one paged graph fetch managed to cover."""

    nodes: list[SystemResult]  # the unclaimed systems, i.e. the graph's nodes
    total: int  # Spansh's count, itself saturating at GRAPH_ROW_CEILING
    truncated: bool  # more systems in range than the ceiling can return
    covered_ly: float | None  # distance of the furthest system actually fetched


def _fetch_graph(reference_system: str, range_ly: int, timeout: float) -> _Graph:
    """Fresh unclaimed systems in range (graph nodes), paged nearest-first up to the ceiling; a modest radius costs one request, only one exceeding GRAPH_ROW_CEILING pays for every page (and still covers ``covered_ly``, not ``range_ly``)."""
    nodes: list[SystemResult] = []
    total = 0
    fetched = 0
    covered_ly: float | None = None
    # Freeze one range for every page so pagination can't shift mid-search.
    updated_range = _fresh_system_range()
    trace.log(
        f"graph: fetching systems updated between "
        f"{updated_range[0]} and {updated_range[1]}"
    )
    for page in range(_MAX_GRAPH_PAGES):
        payload = _query_systems_page(
            reference_system, range_ly, page, updated_range, timeout
        )
        results = payload.get("results", []) or []
        total = int(payload.get("count") or 0)
        fetched += len(results)
        if results:
            # Pages are distance-sorted, so the last row is how far this search reached.
            covered_ly = float_or_none(results[-1].get("distance")) or covered_ly
        unclaimed = [
            _parse_system(raw)
            for raw in results
            # Claimed systems can't be filtered server-side (see _query_systems_page); a missing flag means "not colonised".
            if not raw.get("is_colonised") and not raw.get("is_being_colonised")
        ]
        nodes.extend(unclaimed)
        trace.log(
            f"graph page {page}: {len(results)} rows, {len(results) - len(unclaimed)} "
            f"already claimed, {fetched}/{total} of the range fetched"
        )
        if not results or fetched >= total:
            break
    # ``total`` saturates at GRAPH_ROW_CEILING, so a maxed-out range reports exactly what paging fetched (would read as complete); saturation itself signals "at least this many", never a total.
    truncated = total >= GRAPH_ROW_CEILING or total > fetched
    if truncated:
        reach = f"the nearest {covered_ly:.0f} Ly" if covered_ly is not None else "none"
        trace.log(
            f"graph: CEILING HIT - Spansh returns at most {GRAPH_ROW_CEILING:,} "
            f"rows, so this search covers {reach} of the "
            f"{range_ly} Ly asked for; systems past that are invisible to it"
        )
    trace.log(
        f"graph: {len(nodes)} unclaimed nodes from {fetched} fetched rows, "
        f"covering {covered_ly:.0f} Ly"
        if covered_ly is not None
        else f"graph: {len(nodes)} unclaimed nodes from {fetched} fetched rows"
    )
    return _Graph(nodes=nodes, total=total, truncated=truncated, covered_ly=covered_ly)


def _grid_key(x: float, y: float, z: float) -> tuple[int, int, int]:
    return (
        int(x // CLAIM_RANGE_LY),
        int(y // CLAIM_RANGE_LY),
        int(z // CLAIM_RANGE_LY),
    )


def _compute_steps(nodes: list[SystemResult]) -> None:
    """Multi-source BFS over claim-range hops, writing ``steps`` in place; sources are nodes within claim range of a populated system, the grid cell equals the hop range (27-cell neighbourhood), and claimed nodes are dropped so each level only scans still-unsettled systems."""
    grid: dict[tuple[int, int, int], list[int]] = {}
    for i, n in enumerate(nodes):
        n.steps = None
        if n.x is not None and n.y is not None and n.z is not None:
            grid.setdefault(_grid_key(n.x, n.y, n.z), []).append(i)

    frontier: list[int] = []
    for i, n in enumerate(nodes):
        if (
            n.nearest_populated_ly is not None
            and n.nearest_populated_ly <= CLAIM_RANGE_LY
        ):
            n.steps = 1
            frontier.append(i)
    _drop_claimed(grid, nodes)

    limit_sq = CLAIM_RANGE_LY * CLAIM_RANGE_LY
    level = 1
    while frontier and level < MAX_STEPS:
        next_frontier: list[int] = []
        for i in frontier:
            n = nodes[i]
            if n.x is None or n.y is None or n.z is None:
                continue
            kx, ky, kz = _grid_key(n.x, n.y, n.z)
            for dx in (-1, 0, 1):
                for dy in (-1, 0, 1):
                    for dz in (-1, 0, 1):
                        key = (kx + dx, ky + dy, kz + dz)
                        cell = grid.get(key)
                        if cell is None:
                            continue
                        unclaimed: list[int] = []
                        for j in cell:
                            m = nodes[j]
                            ddx = m.x - n.x  # type: ignore[operator]
                            ddy = m.y - n.y  # type: ignore[operator]
                            ddz = m.z - n.z  # type: ignore[operator]
                            if ddx * ddx + ddy * ddy + ddz * ddz <= limit_sq:
                                m.steps = level + 1
                                next_frontier.append(j)
                            else:
                                unclaimed.append(j)
                        if unclaimed:
                            grid[key] = unclaimed
                        else:
                            del grid[key]
        frontier = next_frontier
        level += 1


def _drop_claimed(
    grid: dict[tuple[int, int, int], list[int]], nodes: list[SystemResult]
) -> None:
    """Remove already-claimed nodes from the grid, dropping emptied cells."""
    for key in list(grid):
        unclaimed = [i for i in grid[key] if nodes[i].steps is None]
        if unclaimed:
            grid[key] = unclaimed
        else:
            del grid[key]


def _step_histogram(reachable: list[SystemResult]) -> str:
    """"1 step: 4 · 2 steps: 91 · …" -- how far out the frontier actually lies."""
    counts = Counter(s.steps for s in reachable)
    if not counts:
        return "no reachable systems"
    return " · ".join(f"{n} step{'s' if n != 1 else ''}: {counts[n]}" for n in sorted(counts))


def _find_agent(system_name: str, timeout: float) -> AgentStation | None:
    """Nearest station with a colonisation contact, else None; Spansh ignores a ``services`` filter, so only ``type`` is sent and the service is matched client-side on the nearest _AGENT_PAGE_SIZE dockables."""
    body = {
        "filters": {"type": {"value": AGENT_STATION_TYPES}},
        "sort": [{"distance": {"direction": "asc"}}],
        "size": _AGENT_PAGE_SIZE,
        "page": 0,
        "reference_system": system_name,
    }
    payload = spansh.post(spansh.STATIONS_URL, body, timeout)
    for raw in payload.get("results", []) or []:
        services = raw.get("services") or []
        if any((s or {}).get("name") == COLONISATION_SERVICE for s in services):
            return AgentStation(
                name=raw.get("name", "") or "",
                system=raw.get("system_name", "") or "",
                distance_ly=float(raw.get("distance", 0.0) or 0.0),
            )
    return None


def _fill_agents(results: list[SystemResult], timeout: float) -> None:
    """Attach the nearest colonisation contact to each displayed candidate; failed lookups mark only their own row (``agent_error``) and aren't cached, so a refresh retries."""
    pending: list[SystemResult] = []
    for r in results:
        key = r.id64 if r.id64 is not None else r.name
        if key in _agent_cache:
            r.agent = _agent_cache[key]
        else:
            pending.append(r)
    if not pending:
        return
    workers = min(_MAX_AGENT_WORKERS, len(pending))
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(_find_agent, r.name, timeout): r for r in pending}
        for fut, r in futures.items():
            try:
                agent = fut.result()
            except urllib.error.HTTPError:
                r.agent_error = True
                continue
            except (urllib.error.URLError, TimeoutError, ValueError):
                r.agent_error = True
                continue
            r.agent = agent
            _agent_cache[r.id64 if r.id64 is not None else r.name] = agent


# Raven verification by id64 is session-stable; cache definitive answers (found/not found), leave failed lookups uncached so a refresh retries.
_verify_cache: dict[int, bool] = {}


def _fill_verification(results: list[SystemResult], timeout: float) -> None:
    """Set ``verified`` by cross-checking Raven Colonial (by id64): on record=verified, nothing=unverified; candidates without an id64 or whose lookup errors stay unchecked (a Raven outage must not dim every row)."""
    pending: list[SystemResult] = []
    for r in results:
        if r.id64 is None:
            continue
        if r.id64 in _verify_cache:
            r.verified = _verify_cache[r.id64]
        else:
            pending.append(r)
    if not pending:
        return
    workers = min(_MAX_AGENT_WORKERS, len(pending))
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {
            pool.submit(raven.fetch_system, r.id64, timeout): r for r in pending
        }
        for fut, r in futures.items():
            try:
                found = fut.result() is not None
            except raven.RavenError:
                continue  # leave verified=None; a refresh retries
            r.verified = found
            _verify_cache[r.id64] = found


def _colonize_sort_key(sort: str, body_weight: float):
    """Sort key for one ranking strategy: ``balanced``=body-weighted score, ``nearest``=pure distance, ``bodies``=body count first; distance and name break ties."""
    inf = float("inf")
    if sort == "nearest":
        return lambda s: (s.distance_ly if s.distance_ly is not None else inf, s.name)
    if sort == "bodies":
        return lambda s: (
            -(s.known_body_count or 0),
            s.distance_ly if s.distance_ly is not None else inf,
            s.name,
        )
    return lambda s: (
        s.weighted_score(body_weight),
        s.distance_ly if s.distance_ly is not None else inf,
        s.name,
    )


def _step_sort_key(sort: str, body_weight: float):
    """Wrap :func:`_colonize_sort_key` to float fewer-step candidates first (any single step beats ``n`` steps, being less work to reach); the strategy orders ties within a step count, ``steps is None`` sorts last."""
    base = _colonize_sort_key(sort, body_weight)
    inf = float("inf")
    return lambda s: (s.steps if s.steps is not None else inf, base(s))


def _verified_sort_key(sort: str, body_weight: float):
    """Compose the ranking layers: Raven-confirmed (``verified is True``) above all unconfirmed (False/None), then :func:`_step_sort_key` fewer-step first, then the strategy within a step count."""
    step = _step_sort_key(sort, body_weight)
    return lambda s: (0 if s.verified is True else 1, step(s))


@dataclass
class FilterResult:
    """Outcome of resolving a candidate pool down to a display page."""

    results: list[SystemResult]  # the ranked, sliced, agent-filled page
    matched: int = 0  # candidates in the pool that passed the filters
    ring_error: bool = False  # the ring lookup failed (results un-ring-filtered)


def _resolve_page(
    pool: list[SystemResult],
    filters: SystemFilters | None,
    *,
    sort: str,
    body_weight: float,
    max_results: int,
    timeout: float,
) -> FilterResult:
    """Filter, rank, verify and slice ``pool`` to one display page; the three network stages (rings, Raven, contacts) each run only on the ranked head of the last, so cost tracks the page not the pool and the pool stays unfiltered."""
    with trace.timed("filters (in memory)") as note:
        if filters is None:
            kept = list(pool)
        else:
            free = replace(filters, verified_only=False, ring_types=())
            kept = [s for s in pool if passes_filters(s, free)]
        note.say(f"{len(kept)}/{len(pool)} candidates pass")
    _trace_census(pool, filters)
    # Rank before the paid stages so they're spent on candidates that reach the page (fewer-step systems the final ranking floats up), not whatever's nearest.
    kept.sort(key=_step_sort_key(sort, body_weight))

    ring_error = False
    if filters is not None and filters.needs_ring_data:
        head = kept[:RING_LOOKUP_LIMIT]
        if len(kept) > len(head):
            trace.log(
                f"rings: {len(kept) - len(head)} candidates ranked below the "
                f"lookup limit ({RING_LOOKUP_LIMIT}) cannot be resolved, dropping"
            )
        try:
            with trace.timed("rings lookup") as note:
                ring_map = get_ring_map([s.name for s in head], timeout)
                ringed = sum(1 for types in ring_map.values() if types)
                note.say(f"{ringed}/{len(ring_map)} resolved systems have rings")
        except SystemSearchError as exc:
            ring_error = True  # leave rings un-filtered rather than blanking the list
            trace.log(f"rings: LOOKUP FAILED ({exc}) - leaving rings unfiltered")
        else:
            stage = replace(filters, verified_only=False)
            kept = [s for s in head if passes_filters(s, stage, ring_map)]
            trace.log(
                f"rings: {len(kept)}/{len(head)} resolved candidates have "
                f"{list(filters.ring_types)}"
            )

    # Confirm a band wider than the page against Raven, then re-rank so confirmed systems sit above Spansh-only records.
    band = kept[: max(max_results, VERIFY_POOL_SIZE)]
    with trace.timed("raven verification") as note:
        _fill_verification(band, timeout)
        confirmed = sum(1 for s in band if s.verified is True)
        unknown = sum(1 for s in band if s.verified is None)
        note.say(f"{confirmed}/{len(band)} confirmed, {unknown} unchecked/failed")
    if filters is not None and filters.verified_only:
        before = len(kept)
        kept = [s for s in kept if s.verified is True]
        trace.log(f"verified_only: {len(kept)}/{before} kept")
    kept.sort(key=_verified_sort_key(sort, body_weight))

    page = kept[:max_results]
    with trace.timed("agent lookups") as note:
        _fill_agents(page, timeout)
        found = sum(1 for s in page if s.agent is not None)
        failed = sum(1 for s in page if s.agent_error)
        note.say(f"{found}/{len(page)} have a contact, {failed} failed")
    trace.log(f"page: showing {len(page)} of {len(kept)} matching candidates")
    return FilterResult(results=page, matched=len(kept), ring_error=ring_error)


def _trace_census(pool: list[SystemResult], filters: SystemFilters | None) -> None:
    """Log which filter is culling the pool, and by how much, on its own."""
    if not trace.enabled() or filters is None:
        return
    # Rings excluded: their data isn't resolved yet, so every candidate would read as ringless and the axis would blame itself; the ring stage reports its own tally later.
    census = filter_census(pool, replace(filters, ring_types=()))
    if not census:
        trace.log("filters: none active, whole pool kept")
        return
    for axis, rejected in sorted(census.items(), key=lambda kv: -kv[1]):
        value = getattr(filters, axis)
        trace.log(f"filter {axis}={value!r} alone rejects {rejected}/{len(pool)}")


def search_colonisation_targets(
    reference_system: str,
    range_ly: int,
    *,
    filters: SystemFilters | None = None,
    sort: str = "balanced",
    body_weight: float = 1.0,
    max_results: int = DEFAULT_RESULTS,
    timeout: float = 20.0,
) -> ColonizeSearch:
    """Rank colonizable systems around ``reference_system``: fetch fresh unclaimed permit-free systems within ``range_ly``, compute claim-range steps, keep those reachable in <=MAX_STEPS as ``pool``, and let :func:`_resolve_page` rank them (Raven-confirmed, then fewer-step, then ``sort``); past GRAPH_ROW_CEILING systems it covers only the nearest and reports ``covered_ly``."""
    reference_system = (reference_system or "").strip()
    if not reference_system:
        raise SystemSearchError("Current star system is unknown yet.")
    range_ly = max(1, int(range_ly))

    trace.log(
        f"=== colonisation search: {reference_system!r} within {range_ly} Ly · "
        f"sort={sort} · body_weight={body_weight} · max_results={max_results} ==="
    )
    trace.dump("active filters:", asdict(filters) if filters is not None else None)
    with trace.timed("=== colonisation search TOTAL"):
        graph = _fetch_graph(reference_system, range_ly, timeout)
        with trace.timed("step graph (BFS over claim-range hops)") as note:
            _compute_steps(graph.nodes)
            reachable = [n for n in graph.nodes if n.steps is not None]
            note.say(
                f"{len(reachable)}/{len(graph.nodes)} reachable in ≤{MAX_STEPS} "
                f"steps · {_step_histogram(reachable)}"
            )
        outcome = _resolve_page(
            reachable,
            filters,
            sort=sort,
            body_weight=body_weight,
            max_results=max_results,
            timeout=timeout,
        )
    return ColonizeSearch(
        results=outcome.results,
        total_in_range=graph.total,
        reachable=len(reachable),
        graph_truncated=graph.truncated,
        covered_ly=graph.covered_ly,
        ring_error=outcome.ring_error,
        # Cache every reachable candidate, not just those matching this search's filters: the overlay re-filters in place, so a narrowed filter could never be loosened again.
        pool=reachable,
    )


def refilter_colonisation(
    pool: list[SystemResult],
    filters: SystemFilters,
    *,
    sort: str = "balanced",
    body_weight: float = 1.0,
    max_results: int = DEFAULT_RESULTS,
    timeout: float = 20.0,
) -> FilterResult:
    """Re-slice the cached pool for a filter change on a worker thread; runs the same pipeline as the original search (free filters in memory, ring/verification/agent lookups reuse session caches), so a change lands on exactly the page a fresh search would."""
    return _resolve_page(
        pool,
        filters,
        sort=sort,
        body_weight=body_weight,
        max_results=max_results,
        timeout=timeout,
    )


# --- Ring composition (bodies endpoint) -----------------------------------
# Systems search carries no ring data; Spansh honours a ``rings`` filter entry only as a presence test, so this fetches ringed bodies of named systems and aggregates classes client-side. Naming keeps it exact (a radius sweep hits the pagination cap ~110 Ly, reading farther systems as ringless); static, so session-cached per system.
_RING_PAGE_SIZE = 500
_MAX_RING_PAGES = 8
_RING_BATCH = 25  # systems per bodies request
_ring_map_cache: dict[str, set[str]] = {}


def get_ring_map(
    system_names: Sequence[str], timeout: float = 20.0
) -> dict[str, set[str]]:
    """Session-cached :func:`fetch_ring_map`, fetching only unseen systems."""
    names = [n for n in dict.fromkeys(system_names) if n]
    missing = [n for n in names if n not in _ring_map_cache]
    if missing:
        _ring_map_cache.update(fetch_ring_map(missing, timeout))
    return {n: _ring_map_cache.get(n, set()) for n in names}


def fetch_ring_map(
    system_names: Sequence[str], timeout: float = 20.0
) -> dict[str, set[str]]:
    """System name -> set of ring classes present; every requested name appears (no rings maps to an empty set), so a caller can cache "looked up, has no rings" distinctly from "never asked"."""
    names = [n for n in dict.fromkeys(system_names) if n]
    result: dict[str, set[str]] = {n: set() for n in names}
    for start in range(0, len(names), _RING_BATCH):
        batch = names[start : start + _RING_BATCH]
        for page in range(_MAX_RING_PAGES):
            body = {
                "filters": {
                    # Presence only: the name is ignored, so this returns every ringed body of the batch.
                    "rings": [{"name": "Icy"}],
                    "system_name": {"value": batch},
                },
                "size": _RING_PAGE_SIZE,
                "page": page,
                "reference_system": batch[0],
            }
            try:
                payload = spansh.post(spansh.BODIES_URL, body, timeout)
            except urllib.error.HTTPError as exc:
                raise SystemSearchError(
                    f"Spansh bodies request failed: HTTP {exc.code}"
                ) from exc
            except (urllib.error.URLError, TimeoutError, ValueError) as exc:
                raise SystemSearchError(f"Spansh bodies request failed: {exc}") from exc
            results = payload.get("results", []) or []
            for b in results:
                types = result.get(b.get("system_name") or "")
                if types is None:
                    continue
                for ring in b.get("rings") or []:
                    rt = ring.get("type")
                    if rt:
                        types.add(rt)
            total = int(payload.get("count") or 0)
            if not results or (page + 1) * _RING_PAGE_SIZE >= total:
                break
    return result
