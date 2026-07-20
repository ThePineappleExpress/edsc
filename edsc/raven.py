"""Read-only client for the Raven Colonial API (https://ravencolonial.com), which tracks colonisation projects with community data often fresher/more complete than Spansh's; EDSC reads it to cross-check and enrich the Spansh system data behind the colonisation search -- most usefully because Raven knows which systems already carry construction sites, a claim signal Spansh can't give reliably (see ``systems._query_systems_page``). No authentication (a commander is identified only by name in the path, so nothing here carries credentials); every field is optional, and posting contributions is a separate opt-in concern living elsewhere."""

# SPDX-License-Identifier: GPL-3.0-or-later

from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, TypeVar

from . import __version__
from .parsing import float_or_none, int_or_none

T = TypeVar("T")

# ``ravencolonial.com`` is the web UI; the API is served from the Azure host behind it -- the base URL the community EDMC plugins use.
API_BASE = (
    "https://ravencolonial100-awcbdvabgze4c5cq.canadacentral-01.azurewebsites.net"
)
USER_AGENT = f"EDSC/{__version__}"

DEFAULT_TIMEOUT = 20.0


class RavenError(RuntimeError):
    """Raised when a Raven Colonial request fails or returns bad data."""


# HTTP codes the v2 system API uses for "this system isn't tracked": an unknown *name* yields 400 (unresolvable), an unknown *id64* yields 404; both mean "no data", so the typed ``fetch_*`` helpers swallow them.
_NOT_TRACKED_CODES = (400, 404)


#  transport


def get(path: str, timeout: float = DEFAULT_TIMEOUT) -> Any:
    """GET ``path`` (relative to :data:`API_BASE`) and decode its JSON body; returns ``None`` for an empty body (e.g. a ``204 No Content`` from the economies endpoint for a known-but-bare system), leaving ``HTTPError`` unraised so the typed ``fetch_*`` helpers map "not tracked" codes to ``None``/empty while every other failure is their concern."""
    req = urllib.request.Request(
        f"{API_BASE}{path}",
        headers={"User-Agent": USER_AGENT, "Accept": "application/json"},
        method="GET",
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        body = resp.read().decode("utf-8")
    return json.loads(body) if body else None


def _system_ref(name_or_id64: str | int) -> str:
    """URL path segment for a system, by name or id64; the v2 system API accepts either -- names percent-encoded for spaces etc., id64s plain decimal."""
    if isinstance(name_or_id64, int):
        return str(name_or_id64)
    return urllib.parse.quote(name_or_id64.strip())


#  parsing (tolerant: every key may be missing)


@dataclass
class RavenBody:
    """One body as Raven records it; ``type``/``subtype`` use Raven's own abbreviated codes (``st`` star, ``elw`` Earth-like, ``gg`` gas giant, ``hmc`` high-metal-content, ...), differing from Spansh's spelled-out ones -- reconcile on ``name`` or count, not these codes."""

    name: str
    num: int | None
    distance_ls: float | None
    type: str
    subtype: str
    features: list[str] = field(default_factory=list)

    @property
    def is_landable(self) -> bool:
        return "landable" in self.features


@dataclass
class RavenSite:
    """A colonisation construction site (planned, in-progress, or complete)."""

    id: str
    name: str
    body_num: int | None
    build_type: str  # "" when the slot has no type assigned yet
    status: str  # e.g. "complete", "build", "plan"
    build_id: str | None  # project GUID; None for sites with no project

    @property
    def is_complete(self) -> bool:
        return self.status == "complete"


@dataclass
class RavenSystem:
    """Raven's view of a system: identity plus its known bodies."""

    name: str
    id64: int | None
    architect: str
    reserve_level: str
    pos: tuple[float, float, float] | None
    bodies: list[RavenBody] = field(default_factory=list)
    revision: int | None = None

    @property
    def body_count(self) -> int:
        return len(self.bodies)


@dataclass
class SpanshEconomy:
    """Raven's snapshot of a station's Spansh economies, for cross-checking; ``economies`` maps an economy name to its percentage share (e.g. ``{"refinery": 100}``), ``updated`` is Raven's timestamp for that snapshot."""

    market_id: int | None
    updated: str
    economies: dict[str, float]


def _parse_body(raw: dict[str, Any]) -> RavenBody:
    return RavenBody(
        name=str(raw.get("name") or ""),
        num=int_or_none(raw.get("num")),
        distance_ls=float_or_none(raw.get("distLS")),
        type=str(raw.get("type") or ""),
        subtype=str(raw.get("subType") or ""),
        features=[str(f) for f in (raw.get("features") or []) if f],
    )


def _parse_pos(raw: object) -> tuple[float, float, float] | None:
    if not isinstance(raw, (list, tuple)) or len(raw) != 3:
        return None
    coords = tuple(float_or_none(c) for c in raw)
    if any(c is None for c in coords):
        return None
    return coords  # type: ignore[return-value]


def _parse_system(raw: dict[str, Any]) -> RavenSystem:
    return RavenSystem(
        name=str(raw.get("name") or ""),
        id64=int_or_none(raw.get("id64")),
        architect=str(raw.get("architect") or ""),
        reserve_level=str(raw.get("reserveLevel") or ""),
        pos=_parse_pos(raw.get("pos")),
        bodies=[_parse_body(b) for b in (raw.get("bodies") or []) if isinstance(b, dict)],
        revision=int_or_none(raw.get("rev")),
    )


def _parse_site(raw: dict[str, Any]) -> RavenSite:
    return RavenSite(
        id=str(raw.get("id") or ""),
        name=str(raw.get("name") or ""),
        body_num=int_or_none(raw.get("bodyNum")),
        build_type=str(raw.get("buildType") or ""),
        status=str(raw.get("status") or ""),
        build_id=(str(raw["buildId"]) if raw.get("buildId") else None),
    )


def _parse_economy(raw: dict[str, Any]) -> SpanshEconomy:
    economies: dict[str, float] = {}
    for name, share in (raw.get("economies") or {}).items():
        pct = float_or_none(share)
        if pct is not None:
            economies[str(name)] = pct
    return SpanshEconomy(
        market_id=int_or_none(raw.get("id")),
        updated=str(raw.get("updated") or ""),
        economies=economies,
    )


#  typed fetch helpers


def _fetch(
    name_or_id64: str | int, suffix: str, noun: str, timeout: float
) -> Any:
    """GET a ``/system/<ref>`` sub-resource, or ``None`` if Raven tracks none; collapses "not tracked" (400/404) and "no content" (a 204 empty body) into the same ``None`` (callers treat them alike), while other transport failures become :class:`RavenError` named after ``noun``."""
    try:
        return get(f"/api/v2/system/{_system_ref(name_or_id64)}{suffix}", timeout)
    except urllib.error.HTTPError as exc:
        if exc.code in _NOT_TRACKED_CODES:
            return None
        raise RavenError(f"Raven {noun} request failed: HTTP {exc.code}") from exc
    except (urllib.error.URLError, TimeoutError, ValueError) as exc:
        raise RavenError(f"Raven {noun} request failed: {exc}") from exc


def _fetch_list(
    name_or_id64: str | int,
    suffix: str,
    noun: str,
    parse: Callable[[dict[str, Any]], T],
    timeout: float,
) -> list[T]:
    """A sub-resource that is a JSON array; empty when Raven tracks none."""
    raw = _fetch(name_or_id64, suffix, noun, timeout)
    if raw is None:
        return []
    if not isinstance(raw, list):
        raise RavenError(f"Raven {noun} response was not a list")
    return [parse(x) for x in raw if isinstance(x, dict)]


def fetch_system(
    name_or_id64: str | int, timeout: float = DEFAULT_TIMEOUT
) -> RavenSystem | None:
    """Raven's record for a system, or ``None`` if it tracks none; an untracked system (400 for an unknown name, 404 for an unknown id64) returns ``None``, any other transport failure is wrapped in :class:`RavenError`."""
    raw = _fetch(name_or_id64, "", "system", timeout)
    if raw is None:
        return None
    if not isinstance(raw, dict):
        raise RavenError("Raven system response was not an object")
    return _parse_system(raw)


def fetch_sites(
    name_or_id64: str | int, timeout: float = DEFAULT_TIMEOUT
) -> list[RavenSite]:
    """Construction sites in a system, empty if none are tracked; an untracked system (400/404) yields an empty list, and a non-empty list is the authoritative "already claimed / being colonised" signal Spansh can't give reliably."""
    return _fetch_list(name_or_id64, "/sites", "sites", _parse_site, timeout)


def fetch_spansh_economies(
    name_or_id64: str | int, timeout: float = DEFAULT_TIMEOUT
) -> list[SpanshEconomy]:
    """Raven's per-station Spansh economy snapshots for a system; empty if none are tracked (400/404) or the system has none (a 204 empty body) -- useful to reconcile the economy data EDSC pulls straight from Spansh against Raven's stored copy."""
    return _fetch_list(
        name_or_id64, "/spanshEconomies", "economies", _parse_economy, timeout
    )
