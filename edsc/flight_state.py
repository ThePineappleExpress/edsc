"""Track whether the ship is flying, from the journal event stream; the gizmos are meaningless while docked (controls locked) and while the game isn't running, so everything here fails *open* -- only a positive statement hides them, so a missed/unknown event never leaves a pilot staring at a blank screen."""

# SPDX-License-Identifier: GPL-3.0-or-later

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, replace

# Landing on a planet is *not* docking: thrusters still answer, so the gizmos stay useful -- only a station lock-out counts.
_DOCKED_EVENTS: dict[str, bool] = {"Docked": True, "Undocked": False}


@dataclass(frozen=True, slots=True)
class ShipStatus:
    """What the journal has told us so far. ``None`` means "not yet said"."""

    docked: bool | None = None
    running: bool | None = None

    @property
    def in_flight(self) -> bool:
        """Whether the gizmos should be showing."""
        if self.running is False:
            return False
        return self.docked is not True


class FlightStateTracker:
    """Fold journal events into a docked/flying answer; replay-safe -- the stream is chronological, so replaying old journals simply converges on the newest state."""

    def __init__(self) -> None:
        self._status = ShipStatus()

    @property
    def status(self) -> ShipStatus:
        return self._status

    @property
    def in_flight(self) -> bool:
        return self._status.in_flight

    def handle(self, event: Mapping[str, object]) -> bool:
        """Fold one journal event. Returns whether ``in_flight`` changed."""
        name = event.get("event")
        if not isinstance(name, str):
            return False
        before = self._status

        if name in _DOCKED_EVENTS:
            # Docking at all proves the game is running.
            self._status = ShipStatus(_DOCKED_EVENTS[name], True)
        elif name == "Location":
            # The only event carrying docked state at session start (LoadGame doesn't); a missing field reads as flying, which fails open.
            self._status = ShipStatus(event.get("Docked") is True, True)
        elif name == "LoadGame":
            self._status = replace(self._status, running=True, docked=None)
        elif name == "Shutdown":
            self._status = ShipStatus(docked=None, running=False)
        else:
            return False
        return self._status.in_flight != before.in_flight

    def seed_docked(self, docked: bool) -> bool:
        """Adopt a docked state learned elsewhere; journal replay never reaches us (the engine only forwards *live* events) but leaves ``AppState.docked_market_id`` correct, so that's where the startup state comes from. Returns whether ``in_flight`` changed."""
        before = self._status.in_flight
        self._status = replace(self._status, docked=docked, running=True)
        return self._status.in_flight != before

    def reset(self) -> None:
        """Forget everything (the journal directory changed)."""
        self._status = ShipStatus()
