"""Domain model: colonisation projects, cargo, and the merge between them.

The model is pure data + logic with no GUI or IO dependencies, so it can be
unit-tested headlessly and reconstructed by replaying journal events in order.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable

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
# Watermark that sorts after every real journal timestamp; used to gate *all*
# replayed CargoTransfer deltas when migrating a pre-watermark cache.
_FUTURE_WATERMARK = "9999-12-31T23:59:59Z"


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
        return sum(l.required for l in self.lines.values())

    def total_provided(self) -> int:
        return sum(min(l.provided, l.required) for l in self.lines.values())

    def progress_fraction(self) -> float:
        """Prefer the game's own progress value; else derive from totals."""
        if self.progress:
            return max(0.0, min(1.0, self.progress))
        total = self.total_required()
        return (self.total_provided() / total) if total else 0.0

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
        # Current player location, learned from FSDJump/Location/CarrierJump (and
        # the system name from Docked). Used as the reference point for the
        # "nearest stations" search. Coords are (x, y, z) in light years.
        self.current_system: str = ""
        self.current_coords: tuple[float, float, float] | None = None
        # market id -> (station_name, system_name) learned from Docked events,
        # so a depot event can be named even if we docked before it fired.
        self._station_names: dict[int, tuple[str, str]] = {}
        # Tombstones for user-removed projects: market id -> removal watermark.
        # Depot events at/before the watermark are ignored so a replay doesn't
        # resurrect the project; docking there again (a newer event) re-adds it.
        self._removed: dict[int, str] = {}

        # Fleet carrier: itemised cargo can't be read from journals directly, so
        # we track it from CargoTransfer deltas (persisted). carrier_total is the
        # authoritative tonnage from CarrierStats, used to flag under-tracking.
        self.carrier_cargo: dict[str, int] = {}
        self.carrier_name: str = ""
        self.carrier_callsign: str = ""
        self.carrier_total: int = 0

        # Watermark: the newest journal timestamp already folded into this state.
        # CargoTransfer is delta-based (not idempotent), so on restart we must
        # NOT re-apply transfers that were already counted into the persisted
        # carrier_cargo. ``_loaded_event_time`` is frozen at load time and gates
        # replayed transfers; ``last_event_time`` advances as new events arrive
        # and is what gets persisted for next launch.
        self.last_event_time: str = ""
        self._loaded_event_time: str = ""

    #  event application 

    def apply_event(self, event: dict[str, Any]) -> bool:
        """Apply one journal event. Returns True if state changed."""
        etype = event.get("event")
        # Advance the persisted watermark. ISO-8601 UTC timestamps sort
        # lexicographically, so a plain string comparison finds the newest.
        ts = event.get("timestamp")
        if isinstance(ts, str) and ts > self.last_event_time:
            self.last_event_time = ts
        if etype == "ColonisationConstructionDepot":
            return self._apply_depot(event)
        if etype == "ColonisationContribution":
            return self._apply_contribution(event)
        if etype == "Docked":
            return self._apply_docked(event)
        if etype in ("FSDJump", "CarrierJump", "Location"):
            return self._apply_location(event)
        if etype == "Cargo":
            # Cargo events describe whichever vessel you're currently in;
            # boarding an SRV must not wipe the tracked ship hold.
            if (event.get("Vessel") or "Ship") != "Ship":
                return False
            # Inline inventory is sometimes present; otherwise the engine reloads
            # Cargo.json separately. An empty list is still an inventory: it
            # means the hold is now empty.
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
        """Track fleet-carrier cargo from ship<->carrier transfer deltas.

        Direction is relative to the destination: ``tocarrier`` adds to the
        carrier, ``toship`` removes from it; ``tosrv`` is the SRV and ignored.
        """
        # Skip transfers already folded into the persisted carrier snapshot.
        # Without this, replaying journal history on startup would re-apply old
        # deltas on top of the loaded amounts and inflate the carrier totals.
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
                self.carrier_cargo[key] = self.carrier_cargo.get(key, 0) + count
                changed = True
            elif direction == "toship":
                remaining = self.carrier_cargo.get(key, 0) - count
                if remaining > 0:
                    self.carrier_cargo[key] = remaining
                else:
                    self.carrier_cargo.pop(key, None)
                changed = True
        return changed

    def _apply_carrier_stats(self, event: dict[str, Any]) -> bool:
        """Capture carrier identity and authoritative total cargo tonnage."""
        changed = False
        name = event.get("Name", "") or ""
        callsign = event.get("Callsign", "") or ""
        total = int((event.get("SpaceUsage") or {}).get("Cargo", 0) or 0)
        if name and name != self.carrier_name:
            self.carrier_name, changed = name, True
        if callsign and callsign != self.carrier_callsign:
            self.carrier_callsign, changed = callsign, True
        if total != self.carrier_total:
            self.carrier_total, changed = total, True
        return changed

    def set_carrier_amount(self, key: str, amount: int) -> None:
        """Manually set the tracked carrier amount for one commodity."""
        if amount > 0:
            self.carrier_cargo[key] = amount
        else:
            self.carrier_cargo.pop(key, None)

    def carrier_tracked_total(self) -> int:
        return sum(self.carrier_cargo.values())

    def finish_replay(self) -> None:
        """Release the replay gate once journal history has been replayed.

        During replay, CargoTransfer deltas at/before the loaded watermark are
        skipped so persisted carrier amounts aren't double-counted. Events that
        arrive after replay come from tailing freshly written journal bytes and
        are never duplicates, so the gate is cleared entirely. (Keeping a
        timestamp cutoff here would silently drop a live transfer landing in
        the same second the replay ended: journal timestamps have 1 s
        resolution.)
        """
        self._loaded_event_time = ""

    def _apply_docked(self, event: dict[str, Any]) -> bool:
        mid = event.get("MarketID")
        if mid is None:
            return False
        name = event.get("StationName", "") or ""
        system = event.get("StarSystem", "") or ""
        changed = False
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
        """Track the player's current system and coordinates.

        FSDJump/CarrierJump/Location all carry ``StarSystem`` and ``StarPos``.
        This is the reference point used by the nearest-stations search; docking
        is no longer required to know where you are.
        """
        changed = False
        system = event.get("StarSystem", "") or ""
        if system and system != self.current_system:
            self.current_system, changed = system, True
        pos = event.get("StarPos")
        if isinstance(pos, (list, tuple)) and len(pos) == 3:
            coords = (float(pos[0]), float(pos[1]), float(pos[2]))
            if coords != self.current_coords:
                self.current_coords, changed = coords, True
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
        """Optimistically bump provided amounts when a delivery is logged.

        The next depot refresh overwrites these with authoritative values; this
        just keeps the overlay responsive the instant you hand cargo over.
        """
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
        """Forget a project (finished, failed, or abandoned).

        A tombstone at the current watermark stops replayed journal history
        from re-adding it; docking at the site again writes a newer depot
        event, which clears the tombstone and brings the project back.
        """
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

    #  queries 

    def project_list(self) -> list[Project]:
        """Projects ordered: active first, then completed/failed, newest first."""
        # Stable sort: order by timestamp descending, then group by status. The
        # second sort preserves newest-first order within each status group.
        projs = sorted(self.projects.values(), key=lambda p: p.updated, reverse=True)
        projs.sort(key=lambda p: 2 if p.failed else (1 if p.complete else 0))
        return projs

    def current_project(self) -> Project | None:
        if self.current_market_id is not None:
            return self.projects.get(self.current_market_id)
        return None

    def active_projects(self) -> list[Project]:
        """Projects that still count toward outstanding needs (not failed)."""
        return [p for p in self.project_list() if not p.failed]

    def outstanding_needs(self) -> dict[str, int]:
        """Commodities still to acquire across all active constructions.

        Maps commodity display name -> tons still short (remaining minus what's
        already in the ship hold and staged on the carrier), aggregated over
        every non-failed project. Only commodities with a positive shortfall are
        included; this is the input to the nearest-stations search.
        """
        needs: dict[str, int] = {}
        for row in self.combined_project().rows(self.cargo, self.carrier_cargo):
            # row.short only subtracts the ship hold (it's the ship-centric
            # "Short" column); carrier stock also counts as already acquired.
            short = row.short - row.on_carrier
            if short > 0:
                needs[row.name] = needs.get(row.name, 0) + short
        return needs

    def combined_project(self) -> Project:
        """A synthetic project aggregating every non-failed construction's needs.

        Required/provided amounts are summed per commodity, so the resulting
        rows show the total still needed across all your constructions, joined
        against your (shared) cargo hold just like a normal project.
        """
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
            "current_system": self.current_system,
            "current_coords": list(self.current_coords)
            if self.current_coords is not None
            else None,
            "cargo": self.cargo,
            "carrier_cargo": self.carrier_cargo,
            "carrier_name": self.carrier_name,
            "carrier_callsign": self.carrier_callsign,
            "carrier_total": self.carrier_total,
            "last_event_time": self.last_event_time,
            # Display names are learned from live journal events only; persist
            # them so names (and Spansh queries) stay correct once the journals
            # that taught us them are gone.
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
                            "key": l.key,
                            "required": l.required,
                            "provided": l.provided,
                            "payment": l.payment,
                        }
                        for l in p.lines.values()
                    ],
                }
                for p in self.projects.values()
            ],
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "AppState":
        """Rebuild state from a persisted dict, skipping malformed entries.

        A single corrupt entry must not brick startup, so each section is
        parsed defensively and dropped on error rather than raised.
        """
        state = cls()
        if not isinstance(data, dict):
            return state
        state.current_market_id = data.get("current_market_id")
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
        # Freeze the loaded watermark so replayed CargoTransfer deltas that were
        # already counted into carrier_cargo above are not applied a second time.
        state.last_event_time = data.get("last_event_time", "") or ""
        if state.last_event_time:
            state._loaded_event_time = state.last_event_time
        elif state.carrier_cargo:
            # Migrating a pre-watermark cache: we can't tell which transfers were
            # already counted, so trust the persisted carrier snapshot and gate
            # every replayed transfer. finish_replay() reopens the gate for live
            # updates once history has been replayed.
            state._loaded_event_time = _FUTURE_WATERMARK
        for pd in data.get("projects", []) or []:
            try:
                proj = Project(
                    market_id=int(pd["market_id"]),
                    station_name=pd.get("station_name", ""),
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
