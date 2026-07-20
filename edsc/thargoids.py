"""Detection of Thargoid encounters from live Elite journal events."""

# SPDX-License-Identifier: GPL-3.0-or-later

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any, ClassVar


class ThargoidEvidence(str, Enum):
    """An unambiguous journal sequence that establishes an encounter."""

    HYPERDICTION = "hyperdiction"
    INTERDICTION = "thargoid_interdiction"
    HOSTILE_NONHUMAN_SIGNAL = "hostile_nonhuman_signal"
    KILL_BOND = "thargoid_kill_bond"
    SYSTEMS_SHUTDOWN = "systems_shutdown"
    ENCOUNTER_ENDED = "encounter_ended"


@dataclass(frozen=True)
class _System:
    name: str = ""
    address: int | str | None = None

    @classmethod
    def from_event(cls, event: dict[str, Any]) -> _System:
        return cls(
            str(event.get("StarSystem") or "").strip(),
            event.get("SystemAddress"),
        )

    @property
    def known(self) -> bool:
        return bool(self.name) or self.address is not None

    def matches(self, other: _System) -> bool:
        if self.address is not None and other.address is not None:
            return self.address == other.address
        return bool(self.name and other.name) and self.name.casefold() == other.name.casefold()


@dataclass(frozen=True)
class _PendingJump:
    origin: _System
    destination: _System


class ThargoidDetector:
    """Correlate live journal events into conservative Thargoid evidence."""

    _END_EVENTS: ClassVar[frozenset[str]] = frozenset({
        "Died",
        "Docked",
        "LoadGame",
        "Resurrect",
        "Shutdown",
        "SupercruiseEntry",
    })

    def __init__(self, current_system: str = "") -> None:
        self.current_system = _System(current_system.strip())
        self._pending_jump: _PendingJump | None = None
        self._nonhuman_instance = False
        self.encounter_active = False

    @property
    def in_nonhuman_instance(self) -> bool:
        """Whether the latest signal-source drop was explicitly nonhuman."""
        return self._nonhuman_instance

    def reset(self, current_system: str = "") -> None:
        """Discard transient evidence and seed the commander's current system."""
        self.current_system = _System(current_system.strip())
        self._pending_jump = None
        self._nonhuman_instance = False
        self.encounter_active = False

    def process(self, event: dict[str, Any]) -> ThargoidEvidence | None:
        """Consume one live event and return newly established evidence."""
        event_type = str(event.get("event") or "")

        if event_type == "StartJump":
            return self._start_jump(event)
        if event_type == "FSDJump":
            return self._finish_jump(event)
        if event_type in {"CarrierJump", "Location"}:
            self.current_system = _System.from_event(event)
            self._pending_jump = None
            return self._end_encounter()

        if event_type in {"USSDrop", "SupercruiseDestinationDrop"}:
            self._nonhuman_instance = _is_nonhuman_signal(event)
            if not self._nonhuman_instance:
                return self._end_encounter()
            return None

        if event_type == "Music" and (
            event.get("MusicTrack") in {"Combat_Unknown", "Unknown_Encounter"}
            and self._nonhuman_instance
        ):
            return self._confirm(ThargoidEvidence.HOSTILE_NONHUMAN_SIGNAL)

        if event_type == "UnderAttack" and self._nonhuman_instance:
            return self._confirm(ThargoidEvidence.HOSTILE_NONHUMAN_SIGNAL)

        if event_type == "Interdicted" and event.get("IsThargoid") is True:
            return self._confirm(ThargoidEvidence.INTERDICTION)

        if event_type == "FactionKillBond" and _has_thargoid_victim(event):
            return self._confirm(ThargoidEvidence.KILL_BOND)

        if event_type == "SystemsShutdown":
            self.encounter_active = True
            return ThargoidEvidence.SYSTEMS_SHUTDOWN

        if event_type in self._END_EVENTS:
            self._pending_jump = None
            self._nonhuman_instance = False
            return self._end_encounter()

        return None

    def _start_jump(self, event: dict[str, Any]) -> ThargoidEvidence | None:
        if event.get("JumpType") != "Hyperspace":
            self._pending_jump = None
            return None
        destination = _System.from_event(event)
        if self.current_system.known and destination.known:
            self._pending_jump = _PendingJump(self.current_system, destination)
        else:
            self._pending_jump = None
        return None

    def _finish_jump(self, event: dict[str, Any]) -> ThargoidEvidence | None:
        arrival = _System.from_event(event)
        pending, self._pending_jump = self._pending_jump, None
        hyperdicted = bool(
            pending is not None
            and arrival.matches(pending.origin)
            and not arrival.matches(pending.destination)
        )
        if arrival.known:
            self.current_system = arrival
        self._nonhuman_instance = False
        if hyperdicted:
            return self._confirm(ThargoidEvidence.HYPERDICTION)
        return self._end_encounter()

    def _confirm(self, evidence: ThargoidEvidence) -> ThargoidEvidence | None:
        if self.encounter_active:
            return None
        self.encounter_active = True
        return evidence

    def _end_encounter(self) -> ThargoidEvidence | None:
        if not self.encounter_active:
            return None
        self.encounter_active = False
        return ThargoidEvidence.ENCOUNTER_ENDED


def _normalise(value: object) -> str:
    return "".join(char for char in str(value or "").casefold() if char.isalnum())


def _is_nonhuman_signal(event: dict[str, Any]) -> bool:
    values = (
        event.get("USSType"),
        event.get("USSType_Localised"),
        event.get("Type"),
        event.get("Type_Localised"),
    )
    return any("nonhuman" in _normalise(value) for value in values)


def _has_thargoid_victim(event: dict[str, Any]) -> bool:
    return any(
        "thargoid" in _normalise(event.get(field))
        for field in ("VictimFaction", "VictimFaction_Localised")
    )
