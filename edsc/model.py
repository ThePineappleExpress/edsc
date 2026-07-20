"""Pure domain model for projects, cargo, and journal events."""

# SPDX-License-Identifier: GPL-3.0-or-later

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Any

from .commodities import (
    canonical_name,
    display_name,
    register_display_name,
    registry_snapshot,
    restore_registry,
)

# Sentinel market id for the synthetic "All constructions" aggregate project.
COMBINED_MARKET_ID = -1
# Sentinel tab id for the "nearest stations" search view (not a real market).
STATIONS_MARKET_ID = -2
# Sentinel tab id for the "colonization targets" search view (not a real market).
COLONIZE_MARKET_ID = -3
# Watermark sorting after every real journal timestamp; gates *all* replayed CargoTransfer deltas when migrating a pre-watermark cache.
_FUTURE_WATERMARK = "9999-12-31T23:59:59Z"


# Leading localisation token in a journal StationName, e.g. "$EXT_PANEL_ColonisationShip; Nearchus Gateway".
_STATION_TOKEN_RE = re.compile(r"^\$(?P<token>[^;]*);\s*(?P<rest>.*)$")


def _prefix_from_token(token: str) -> str:
    """Readable site-type prefix from a localisation token body (``EXT_PANEL_ColonisationShip`` -> ``Colonisation Ship``); derived not mapped so future tokens get a sensible prefix too."""
    token = token.split(":", 1)[0]  # drop ":#index=1"-style suffixes
    token = token.removeprefix("EXT_PANEL_").replace("_", " ")
    return re.sub(r"(?<=[a-z])(?=[A-Z])", " ", token).strip()


def clean_station_name(name: str, localised: str = "") -> str:
    """Human-readable station name from a raw journal StationName; a Colonisation Ship name like ``$EXT_PANEL_ColonisationShip; Nearchus Gateway`` (no _Localised fallback) renders as "<derived site-type prefix>: <name>", a bare token falling back to localised, then prefix, then the raw value."""
    m = _STATION_TOKEN_RE.match(name)
    if m is None:
        return name
    rest = m.group("rest").strip()
    prefix = _prefix_from_token(m.group("token"))
    if rest:
        return f"{prefix}: {rest}" if prefix else rest
    return localised or prefix or name


def _int_map(data: Any) -> dict[str, int]:
    """A str->int dict from persisted data, dropping malformed entries."""
    out: dict[str, int] = {}
    if isinstance(data, dict):
        for k, v in data.items():
            try:
                out[str(k)] = int(v)
            except (TypeError, ValueError):
                continue
    return out


@dataclass
class CommodityLine:
    """A single commodity a project requires."""

    key: str
    required: int = 0
    provided: int = 0  # amount already delivered to the construction depot
    payment: int = 0  # credits paid per unit delivered (from the depot event)

    @property
    def remaining(self) -> int:
        """Units still to be delivered to complete this line."""
        return max(0, self.required - self.provided)

    @property
    def done(self) -> bool:
        return self.provided >= self.required and self.required > 0


@dataclass
class CommodityRow:
    """A flattened, display-ready view of one commodity for one project."""

    key: str
    name: str
    required: int
    provided: int
    in_cargo: int
    remaining: int  # still to deliver (required - provided)
    short: int  # still to acquire (remaining - in_cargo), clamped at 0
    on_carrier: int = 0  # tracked amount staged on the fleet carrier

    @property
    def done(self) -> bool:
        return self.remaining == 0 and self.required > 0

    @property
    def can_complete_now(self) -> bool:
        """You are carrying enough to finish this line right now."""
        return not self.done and self.in_cargo >= self.remaining

    @property
    def covered_by_stock(self) -> bool:
        """Ship hold + carrier together cover what's still needed."""
        return not self.done and (self.in_cargo + self.on_carrier) >= self.remaining


@dataclass
class Project:
    """A colonisation construction site and the materials it needs."""

    market_id: int
    station_name: str = ""
    system_name: str = ""
    progress: float = 0.0  # 0..1, from the depot event
    complete: bool = False
    failed: bool = False
    updated: str = ""  # timestamp of the last depot event
    lines: dict[str, CommodityLine] = field(default_factory=dict)

    @property
    def title(self) -> str:
        if self.station_name and self.system_name:
            return f"{self.station_name} ({self.system_name})"
        return self.station_name or self.system_name or f"Depot {self.market_id}"

    def total_required(self) -> int:
        return sum(line.required for line in self.lines.values())

    def total_provided(self) -> int:
        return sum(
            min(line.provided, line.required) for line in self.lines.values()
        )

    def progress_fraction(self) -> float:
        """Prefer the game's own progress value; else derive from totals."""
        if self.progress:
            return max(0.0, min(1.0, self.progress))
        total = self.total_required()
        return (self.total_provided() / total) if total else 0.0

    @property
    def all_delivered(self) -> bool:
        """Every required commodity has been delivered in full."""
        total = self.total_required()
        return total > 0 and self.total_provided() >= total

    def rows(
        self, cargo: dict[str, int], carrier: dict[str, int] | None = None
    ) -> list[CommodityRow]:
        """Display rows joined against the ship hold and (optional) carrier."""
        carrier = carrier or {}
        out: list[CommodityRow] = []
        for key, line in self.lines.items():
            in_cargo = cargo.get(key, 0)
            remaining = line.remaining
            out.append(
                CommodityRow(
                    key=key,
                    name=display_name(key),
                    required=line.required,
                    provided=line.provided,
                    in_cargo=in_cargo,
                    remaining=remaining,
                    short=max(0, remaining - in_cargo),
                    on_carrier=carrier.get(key, 0),
                )
            )
        # Outstanding first, then by name; completed lines sink to the bottom.
        out.sort(key=lambda r: (r.done, r.name.lower()))
        return out


class AppState:
    """Everything EDSC knows: projects keyed by market id, plus current cargo."""

    def __init__(self) -> None:
        self.projects: dict[int, Project] = {}
        self.cargo: dict[str, int] = {}
        self.current_market_id: int | None = None
        # Current player location from FSDJump/Location/CarrierJump (system name also from Docked); reference point for the "nearest stations" search, coords (x, y, z) in Ly.
        self.current_system: str = ""
        self.current_coords: tuple[float, float, float] | None = None
        # Where the ship is docked now (None in flight) and the in-stock commodities at that market from Market.json (to highlight buyable project lines); the snapshot keeps its own market id since Market.json describes the last market *opened*, not necessarily where we are.
        self.docked_market_id: int | None = None
        self._market_id: int | None = None
        self._market_stock: set[str] = set()
        # market id -> (station_name, system_name) from Docked events, so a depot event can be named even if we docked before it fired.
        self._station_names: dict[int, tuple[str, str]] = {}
        # Tombstones for user-removed projects (market id -> removal watermark); depot events at/before it are ignored so a replay can't resurrect the project, docking again (newer event) re-adds it.
        self._removed: dict[int, str] = {}

        # Fleet carrier: itemised cargo isn't in journals, so tracked from persisted CargoTransfer deltas; carrier_total is tonnage aboard, re-based by CarrierStats and moved by transfers between (CarrierStats only fires on opening carrier management, else stale for days).
        self.carrier_cargo: dict[str, int] = {}
        self.carrier_name: str = ""
        self.carrier_callsign: str = ""
        self.carrier_total: int = 0
        # Usable cargo space from CarrierStats, not the 25,000 t hull figure (services/packs eat into it, so it rises when uninstalled); 0 = unknown (no CarrierStats yet), which disables the fit checks below.
        self.carrier_capacity: int = 0

        # Watermark: newest journal timestamp already folded into this state; CargoTransfer is delta-based, so ``_loaded_event_time`` (frozen at load) gates replayed transfers from double-counting, while ``last_event_time`` advances and is persisted for next launch.
        self.last_event_time: str = ""
        self._loaded_event_time: str = ""

    #  event application 

    def apply_event(self, event: dict[str, Any]) -> bool:
        """Apply one journal event. Returns True if state changed."""
        etype = event.get("event")
        # Advance the persisted watermark; ISO-8601 UTC timestamps sort lexicographically, so a string compare finds the newest.
        ts = event.get("timestamp")
        if isinstance(ts, str) and ts > self.last_event_time:
            self.last_event_time = ts
        if etype == "ColonisationConstructionDepot":
            return self._apply_depot(event)
        if etype == "ColonisationContribution":
            return self._apply_contribution(event)
        if etype == "Docked":
            return self._apply_docked(event)
        if etype == "Undocked":
            return self._set_docked(None)
        if etype in ("FSDJump", "CarrierJump", "Location"):
            return self._apply_location(event)
        if etype == "Cargo":
            # Cargo events describe whichever vessel you're in; boarding an SRV must not wipe the tracked ship hold.
            if (event.get("Vessel") or "Ship") != "Ship":
                return False
            # Inline inventory is sometimes present, else the engine reloads Cargo.json; an empty list is still an inventory (the hold is now empty).
            inv = event.get("Inventory")
            if inv is None:
                return False
            self.set_cargo(inv)
            return True
        if etype == "CargoTransfer":
            return self._apply_cargo_transfer(event)
        if etype == "CarrierStats":
            return self._apply_carrier_stats(event)
        return False

    def _apply_cargo_transfer(self, event: dict[str, Any]) -> bool:
        """Track fleet-carrier cargo from ship<->carrier transfer deltas: ``tocarrier`` adds, ``toship`` removes, ``tosrv`` ignored; a ``tocarrier`` is credited only as far as the carrier has room, since the game journals every *requested* transfer including full-carrier refusals (arriving in bursts as the player re-clicks) that never left the ship."""
        # Skip transfers already folded into the persisted carrier snapshot, else startup replay re-applies old deltas on top and inflates the totals.
        ts = event.get("timestamp", "")
        if ts and self._loaded_event_time and ts <= self._loaded_event_time:
            return False
        changed = False
        for t in event.get("Transfers", []) or []:
            direction = t.get("Direction")
            key = register_display_name(t.get("Type"), t.get("Type_Localised"))
            count = int(t.get("Count", 0) or 0)
            if not key or not count:
                continue
            if direction == "tocarrier":
                count = self._accepted_by_carrier(count)
                if not count:
                    continue
                self.carrier_cargo[key] = self.carrier_cargo.get(key, 0) + count
                self.carrier_total += count
                changed = True
            elif direction == "toship":
                remaining = self.carrier_cargo.get(key, 0) - count
                if remaining > 0:
                    self.carrier_cargo[key] = remaining
                else:
                    self.carrier_cargo.pop(key, None)
                self.carrier_total = max(0, self.carrier_total - count)
                changed = True
        return changed

    def _accepted_by_carrier(self, count: int) -> int:
        """How much of a ``count`` t transfer the carrier still has room for; accepts the lot while capacity is unknown (no CarrierStats to check against, and dropping real transfers beats the over-count this guards)."""
        if not self.carrier_capacity:
            return count
        return max(0, min(count, self.carrier_capacity - self.carrier_total))

    def _apply_carrier_stats(self, event: dict[str, Any]) -> bool:
        """Capture carrier identity, tonnage aboard and usable cargo capacity; the authoritative re-base where the game's numbers win over whatever the transfer deltas made of them since the last snapshot."""
        changed = False
        name = event.get("Name", "") or ""
        callsign = event.get("Callsign", "") or ""
        usage = event.get("SpaceUsage") or {}
        total = int(usage.get("Cargo", 0) or 0)
        # Cargo + FreeSpace, not TotalCapacity (the hull figure counts services/packs that can't hold cargo); without FreeSpace, keep the last known capacity, since Cargo alone would read as "full" and refuse every transfer.
        free = usage.get("FreeSpace")
        capacity = total + int(free or 0) if free is not None else self.carrier_capacity
        if name and name != self.carrier_name:
            self.carrier_name, changed = name, True
        if callsign and callsign != self.carrier_callsign:
            self.carrier_callsign, changed = callsign, True
        if total != self.carrier_total:
            self.carrier_total, changed = total, True
        if capacity != self.carrier_capacity:
            self.carrier_capacity, changed = capacity, True
        return changed

    def set_carrier_amount(self, key: str, amount: int) -> None:
        """Manually set the tracked carrier amount for one commodity."""
        if amount > 0:
            self.carrier_cargo[key] = amount
        else:
            self.carrier_cargo.pop(key, None)

    def carrier_tracked_total(self) -> int:
        return sum(self.carrier_cargo.values())

    def carrier_free_space(self) -> int:
        """Tons the carrier can still take, or 0 while capacity is unknown."""
        if not self.carrier_capacity:
            return 0
        return max(0, self.carrier_capacity - self.carrier_total)

    def finish_replay(self) -> None:
        """Release the replay gate once history is replayed; replay skips CargoTransfer deltas at/before the loaded watermark to avoid double-counting, but post-replay events tail fresh bytes and are never duplicates, so the gate clears entirely (a timestamp cutoff would drop a live transfer in the same 1 s-resolution second replay ended)."""
        self._loaded_event_time = ""

    def _set_docked(self, market_id: int | None) -> bool:
        if market_id == self.docked_market_id:
            return False
        self.docked_market_id = market_id
        return True

    def _apply_docked(self, event: dict[str, Any]) -> bool:
        mid = event.get("MarketID")
        if mid is None:
            return False
        name = clean_station_name(
            event.get("StationName", "") or "",
            event.get("StationName_Localised", "") or "",
        )
        system = event.get("StarSystem", "") or ""
        changed = self._set_docked(mid)
        if self._station_names.get(mid) != (name, system):
            self._station_names[mid] = (name, system)
            changed = True
        if mid != self.current_market_id:
            self.current_market_id = mid
            changed = True
        if system and system != self.current_system:
            self.current_system, changed = system, True
        proj = self.projects.get(mid)
        if proj is not None:
            if name and proj.station_name != name:
                proj.station_name, changed = name, True
            if system and proj.system_name != system:
                proj.system_name, changed = system, True
        return changed

    def _apply_location(self, event: dict[str, Any]) -> bool:
        """Track the player's current system and coordinates from FSDJump/CarrierJump/Location (all carry ``StarSystem``/``StarPos``); the reference point for the nearest-stations search, so docking isn't required to know where you are."""
        changed = False
        system = event.get("StarSystem", "") or ""
        if system and system != self.current_system:
            self.current_system, changed = system, True
        pos = event.get("StarPos")
        if isinstance(pos, (list, tuple)) and len(pos) == 3:
            coords = (float(pos[0]), float(pos[1]), float(pos[2]))
            if coords != self.current_coords:
                self.current_coords, changed = coords, True
        # Location/CarrierJump carry a Docked flag (+ MarketID when docked); FSDJump has none (flight only); either way this re-anchors docked state after a session restart.
        docked_mid = event.get("MarketID") if event.get("Docked") else None
        changed = self._set_docked(docked_mid) or changed
        return changed

    def _apply_depot(self, event: dict[str, Any]) -> bool:
        mid = event.get("MarketID")
        if mid is None:
            return False
        tombstone = self._removed.get(mid)
        if tombstone:
            ts = event.get("timestamp", "")
            if not ts or ts <= tombstone:
                return False  # replayed history for a removed project
            del self._removed[mid]  # newer depot event: the user went back
        station, system = self._station_names.get(mid, ("", ""))
        proj = self.projects.get(mid)
        if proj is None:
            proj = Project(market_id=mid, station_name=station, system_name=system)
            self.projects[mid] = proj
        else:
            if station and not proj.station_name:
                proj.station_name = station
            if system and not proj.system_name:
                proj.system_name = system

        proj.progress = float(event.get("ConstructionProgress", proj.progress) or 0.0)
        proj.complete = bool(event.get("ConstructionComplete", proj.complete))
        proj.failed = bool(event.get("ConstructionFailed", proj.failed))
        proj.updated = event.get("timestamp", proj.updated)
        self.current_market_id = mid

        # The depot event is the authoritative snapshot: rebuild the line set.
        new_lines: dict[str, CommodityLine] = {}
        for res in event.get("ResourcesRequired", []) or []:
            key = register_display_name(res.get("Name"), res.get("Name_Localised"))
            if not key:
                continue
            new_lines[key] = CommodityLine(
                key=key,
                required=int(res.get("RequiredAmount", 0) or 0),
                provided=int(res.get("ProvidedAmount", 0) or 0),
                payment=int(res.get("Payment", 0) or 0),
            )
        proj.lines = new_lines
        return True

    def _apply_contribution(self, event: dict[str, Any]) -> bool:
        """Optimistically bump provided amounts when a delivery is logged; the next depot refresh overwrites them authoritatively, this just keeps the overlay responsive the instant you hand cargo over."""
        mid = event.get("MarketID")
        proj = self.projects.get(mid) if mid is not None else None
        if proj is None:
            return False
        changed = False
        for c in event.get("Contributions", []) or []:
            key = register_display_name(c.get("Name"), c.get("Name_Localised"))
            line = proj.lines.get(key)
            if line is None:
                continue
            amount = int(c.get("Amount", 0) or 0)
            if amount:
                line.provided = min(line.required, line.provided + amount)
                changed = True
        return changed

    def remove_project(self, market_id: int) -> bool:
        """Forget a project (finished, failed, or abandoned); a tombstone at the current watermark stops replay from re-adding it, and docking again (a newer depot event) clears it and brings the project back."""
        existed = self.projects.pop(market_id, None) is not None
        self._station_names.pop(market_id, None)
        if existed and self.last_event_time:
            self._removed[market_id] = self.last_event_time
        if self.current_market_id == market_id:
            self.current_market_id = None
        return existed

    def set_cargo(self, inventory: Iterable[dict[str, Any]]) -> None:
        """Replace the cargo hold from a Cargo.json / Cargo-event inventory list."""
        new_cargo: dict[str, int] = {}
        for item in inventory or []:
            key = register_display_name(item.get("Name"), item.get("Name_Localised"))
            if not key:
                continue
            new_cargo[key] = new_cargo.get(key, 0) + int(item.get("Count", 0) or 0)
        self.cargo = new_cargo

    def set_market(self, data: dict[str, Any]) -> None:
        """Replace the station-market snapshot from a Market.json dict; keeps the market id and commodities actually in stock (Stock > 0), not items the station merely buys."""
        stock: set[str] = set()
        for item in data.get("Items") or []:
            key = register_display_name(item.get("Name"), item.get("Name_Localised"))
            try:
                in_stock = int(item.get("Stock", 0) or 0) > 0
            except (TypeError, ValueError):
                in_stock = False
            if key and in_stock:
                stock.add(key)
        mid = data.get("MarketID")
        self._market_id = mid if isinstance(mid, int) else None
        self._market_stock = stock

    #  queries

    def docked_station_stock(self) -> set[str]:
        """Commodity keys in stock at the station we're docked at now; empty in flight, and while the Market.json snapshot still describes a previously visited market (it updates only when the commodities market is opened)."""
        if self.docked_market_id is None or self._market_id != self.docked_market_id:
            return set()
        return self._market_stock


    def project_list(self) -> list[Project]:
        """Projects ordered: active first, then completed/failed, newest first."""
        # Stable sort: timestamp desc, then group by status (the second sort preserves newest-first within each group).
        projs = sorted(self.projects.values(), key=lambda p: p.updated, reverse=True)
        projs.sort(key=lambda p: 2 if p.failed else (1 if p.complete else 0))
        return projs

    def active_projects(self) -> list[Project]:
        """Projects that still count toward outstanding needs (not failed)."""
        return [p for p in self.project_list() if not p.failed]

    def outstanding_needs(self) -> dict[str, int]:
        """Commodities still to acquire across all active constructions: display name -> tons short (remaining minus ship hold and carrier), aggregated over every non-failed project, positive shortfalls only; the input to the nearest-stations search."""
        needs: dict[str, int] = {}
        for row in self.combined_project().rows(self.cargo, self.carrier_cargo):
            # row.short subtracts only the ship hold (the ship-centric "Short" column); carrier stock also counts as already acquired.
            short = row.short - row.on_carrier
            if short > 0:
                needs[row.name] = needs.get(row.name, 0) + short
        return needs

    def combined_project(self) -> Project:
        """A synthetic project aggregating every non-failed construction's needs; required/provided are summed per commodity so rows show the total still needed across all constructions, joined against the shared cargo hold like a normal project."""
        combined = Project(
            market_id=COMBINED_MARKET_ID, station_name="All constructions"
        )
        for proj in self.active_projects():
            for key, line in proj.lines.items():
                agg = combined.lines.get(key)
                if agg is None:
                    agg = CommodityLine(key=key)
                    combined.lines[key] = agg
                agg.required += line.required
                agg.provided += min(line.provided, line.required)
                agg.payment = max(agg.payment, line.payment)
        return combined

    #  persistence 

    def to_dict(self) -> dict[str, Any]:
        return {
            "current_market_id": self.current_market_id,
            "docked_market_id": self.docked_market_id,
            "current_system": self.current_system,
            "current_coords": list(self.current_coords)
            if self.current_coords is not None
            else None,
            "cargo": self.cargo,
            "carrier_cargo": self.carrier_cargo,
            "carrier_name": self.carrier_name,
            "carrier_callsign": self.carrier_callsign,
            "carrier_total": self.carrier_total,
            "carrier_capacity": self.carrier_capacity,
            "last_event_time": self.last_event_time,
            # Display names are learned from live journal events only; persist them so names (and Spansh queries) stay correct once those journals are gone.
            "display_names": registry_snapshot(),
            "removed_projects": {
                str(mid): ts for mid, ts in self._removed.items()
            },
            "projects": [
                {
                    "market_id": p.market_id,
                    "station_name": p.station_name,
                    "system_name": p.system_name,
                    "progress": p.progress,
                    "complete": p.complete,
                    "failed": p.failed,
                    "updated": p.updated,
                    "lines": [
                        {
                            "key": line.key,
                            "required": line.required,
                            "provided": line.provided,
                            "payment": line.payment,
                        }
                        for line in p.lines.values()
                    ],
                }
                for p in self.projects.values()
            ],
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> AppState:
        """Rebuild state from a persisted dict, skipping malformed entries; each section is parsed defensively and dropped on error so one corrupt entry can't brick startup."""
        state = cls()
        if not isinstance(data, dict):
            return state
        state.current_market_id = data.get("current_market_id")
        docked = data.get("docked_market_id")
        state.docked_market_id = docked if isinstance(docked, int) else None
        state.current_system = data.get("current_system", "") or ""
        coords = data.get("current_coords")
        if isinstance(coords, (list, tuple)) and len(coords) == 3:
            try:
                state.current_coords = (
                    float(coords[0]),
                    float(coords[1]),
                    float(coords[2]),
                )
            except (TypeError, ValueError):
                state.current_coords = None
        state.cargo = _int_map(data.get("cargo"))
        state.carrier_cargo = _int_map(data.get("carrier_cargo"))
        state.carrier_name = data.get("carrier_name", "") or ""
        state.carrier_callsign = data.get("carrier_callsign", "") or ""
        try:
            state.carrier_total = int(data.get("carrier_total", 0) or 0)
        except (TypeError, ValueError):
            state.carrier_total = 0
        try:
            state.carrier_capacity = int(data.get("carrier_capacity", 0) or 0)
        except (TypeError, ValueError):
            state.carrier_capacity = 0
        restore_registry(
            data.get("display_names")
            if isinstance(data.get("display_names"), dict)
            else None
        )
        removed = data.get("removed_projects")
        if isinstance(removed, dict):
            for mid, ts in removed.items():
                try:
                    state._removed[int(mid)] = str(ts)
                except (TypeError, ValueError):
                    continue
        # Freeze the loaded watermark so replayed CargoTransfer deltas already counted into carrier_cargo above aren't applied twice.
        state.last_event_time = data.get("last_event_time", "") or ""
        if state.last_event_time:
            state._loaded_event_time = state.last_event_time
        elif state.carrier_cargo:
            # Migrating a pre-watermark cache: can't tell which transfers were counted, so trust the persisted snapshot and gate every replayed transfer (finish_replay() reopens the gate for live updates).
            state._loaded_event_time = _FUTURE_WATERMARK
        for pd in data.get("projects", []) or []:
            try:
                proj = Project(
                    market_id=int(pd["market_id"]),
                    # Cleaned again on load so token names persisted by older versions heal without a re-dock.
                    station_name=clean_station_name(pd.get("station_name", "") or ""),
                    system_name=pd.get("system_name", ""),
                    progress=float(pd.get("progress", 0.0) or 0.0),
                    complete=bool(pd.get("complete", False)),
                    failed=bool(pd.get("failed", False)),
                    updated=pd.get("updated", ""),
                )
                for ld in pd.get("lines", []) or []:
                    key = ld.get("key") or canonical_name(ld.get("name"))
                    if not key:
                        continue
                    proj.lines[key] = CommodityLine(
                        key=key,
                        required=int(ld.get("required", 0) or 0),
                        provided=int(ld.get("provided", 0) or 0),
                        payment=int(ld.get("payment", 0) or 0),
                    )
            except (AttributeError, KeyError, TypeError, ValueError):
                continue  # one corrupt project must not brick the whole cache
            state.projects[proj.market_id] = proj
            if proj.station_name or proj.system_name:
                state._station_names[proj.market_id] = (
                    proj.station_name,
                    proj.system_name,
                )
        return state
