"""Engine-boost cooldown readiness; Elite gives each hull a fixed *boost interval* (minimum time between engine boosts) independent of the power distributor, so the indicator is a per-ship countdown started by each observed boost press, not a distributor calculation. ``BOOST_INTERVAL`` (seconds) is EDSY's ``boostint`` ("Minimum time between engine boosts"), keyed by the journal ``Ship`` symbol (lower-cased); ships absent here leave the indicator greyed rather than guessed. Data source: EDSY (edsy.org) ship data, ``boostint`` per hull -- EDSY is copyright taleden under CC BY-NC, these are game facts re-tabulated with credit."""

# SPDX-License-Identifier: GPL-3.0-or-later

from __future__ import annotations

from collections.abc import Mapping
from enum import Enum

# Minimum seconds between boosts, per ship. From EDSY's ``boostint``.
BOOST_INTERVAL: dict[str, float] = {
    "adder": 4.0,
    "anaconda": 6.0,
    "asp": 4.5,
    "asp_scout": 4.5,
    "belugaliner": 6.0,
    "cobramkiii": 5.0,
    "cobramkiv": 5.0,
    "cobramkv": 5.0,
    "corsair": 5.0,
    "cutter": 6.0,
    "diamondback": 4.0,
    "diamondbackxl": 4.0,
    "dolphin": 4.0,
    "eagle": 4.5,
    "empire_courier": 4.0,
    "empire_eagle": 4.5,
    "empire_trader": 4.5,
    "explorer_nx": 5.5,
    "federation_corvette": 6.0,
    "federation_dropship": 6.0,
    "federation_dropship_mkii": 6.0,
    "federation_gunship": 6.0,
    "ferdelance": 5.0,
    "hauler": 4.0,
    "independant_trader": 4.0,
    "krait_light": 4.5,
    "krait_mkii": 4.5,
    "lakonminer": 6.0,
    "mamba": 5.0,
    "mandalay": 5.0,
    "mediumtransport01": 5.0,
    "orca": 4.0,
    "panthermkii": 6.5,
    "python": 4.5,
    "python_nx": 5.0,
    "sidewinder": 4.0,
    "smallcombat01_nx": 4.5,
    "type6": 4.0,
    "type7": 6.0,
    "type8": 6.0,
    "type9": 6.0,
    "type9_military": 6.0,
    "typex": 6.0,
    "typex_2": 6.0,
    "typex_3": 6.0,
    "viper": 5.0,
    "viper_mkiv": 5.0,
    "vulture": 4.5,
}

# Below this remaining time the indicator flips to its "about to be ready" hue.
IMMINENT_SECONDS = 1.0


def boost_interval_for(ship: str | None) -> float | None:
    """Minimum seconds between boosts for this hull, or ``None`` if unknown."""
    if not ship:
        return None
    return BOOST_INTERVAL.get(ship.lower())


def read_loadout(event: Mapping[str, object]) -> str | None:
    """The ship symbol from a ``Loadout`` event, or ``None``."""
    ship = event.get("Ship")
    return ship if isinstance(ship, str) else None


class BoostState(Enum):
    """What the indicator shows."""

    READY = "ready"  # green
    COOLING = "cooling"  # red
    IMMINENT = "imminent"  # yellow -- under a second to go
    UNAVAILABLE = "unavailable"  # grey -- unknown ship


class BoostTracker:
    """Live boost readiness: a fixed per-ship countdown from each boost press; readiness can't be read from the game (no boost event or status flag), so it's timed from observed presses of the boost button, and a press while still cooling (a no-op in-game) is ignored here too."""

    def __init__(self) -> None:
        self._interval: float | None = None
        self._remaining: float = 0.0  # seconds until ready; 0 == ready

    #  inputs

    def set_ship(self, ship: str | None) -> None:
        """Adopt a new hull (from a Loadout event); resets to ready on change."""
        interval = boost_interval_for(ship)
        if interval != self._interval:
            self._interval = interval
            self._remaining = 0.0

    def boost(self) -> bool:
        """Register an observed boost press. Returns whether it counted."""
        if not self.available or self._remaining > 0.0:
            return False
        self._remaining = self._interval
        return True

    def advance(self, dt: float) -> None:
        """Count the cooldown down by ``dt`` seconds."""
        if dt > 0.0 and self._remaining > 0.0:
            self._remaining = max(0.0, self._remaining - dt)

    #  outputs

    @property
    def available(self) -> bool:
        """Whether we have a boost interval for the current hull."""
        return self._interval is not None

    @property
    def remaining(self) -> float:
        """Seconds until the next boost is ready (0 when ready)."""
        return self._remaining

    @property
    def interval(self) -> float | None:
        """The current hull's boost interval, for readouts."""
        return self._interval

    @property
    def fraction(self) -> float:
        """Charge toward the next boost, 0..1 (1 == ready)."""
        if not self.available or self._interval <= 0.0:
            return 1.0
        return max(0.0, min(1.0, 1.0 - self._remaining / self._interval))

    @property
    def state(self) -> BoostState:
        if not self.available:
            return BoostState.UNAVAILABLE
        if self._remaining <= 0.0:
            return BoostState.READY
        if self._remaining <= IMMINENT_SECONDS:
            return BoostState.IMMINENT
        return BoostState.COOLING
