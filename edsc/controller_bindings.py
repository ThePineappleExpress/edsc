"""Persisted discrete controller shortcuts and event matching."""

# SPDX-License-Identifier: GPL-3.0-or-later

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Literal

from .platform.controller import ControllerEvent, hat_direction

BindingKind = Literal["button", "hat"]

# Deliberately small initial action set; ordering is also the settings order and the deterministic priority used if a hand-edited config has duplicates.
CONTROLLER_ACTIONS: tuple[tuple[str, str], ...] = (
    ("previous_tab", "Previous tab"),
    ("next_tab", "Next tab"),
    ("toggle_collapsed", "Collapse / expand overlay"),
    ("toggle_opacity", "Toggle full opacity"),
    ("refresh_search", "Refresh active search"),
)
CONTROLLER_ACTION_IDS = frozenset(action_id for action_id, _ in CONTROLLER_ACTIONS)

@dataclass(frozen=True, slots=True)
class ControllerBinding:
    """One device-local discrete controller shortcut."""

    kind: BindingKind
    index: int
    value: int

    @classmethod
    def from_config(cls, raw: object) -> ControllerBinding | None:
        """Parse one untrusted JSON value, returning ``None`` when invalid."""
        if not isinstance(raw, Mapping):
            return None
        kind = raw.get("kind")
        index = raw.get("index")
        value = raw.get("value")
        if kind not in ("button", "hat"):
            return None
        if isinstance(index, bool) or not isinstance(index, int) or index < 0:
            return None
        if isinstance(value, bool) or not isinstance(value, int):
            return None
        if kind == "button" and value != 1:
            return None
        if kind == "hat" and not 1 <= value <= 0x0F:
            return None
        return cls(kind, index, value)

    @classmethod
    def from_event(cls, event: ControllerEvent) -> ControllerBinding | None:
        """Create a binding from an activation event suitable for recording."""
        if event.initial or event.index < 0:
            return None
        if event.kind == "button" and event.value:
            return cls("button", event.index, 1)
        if event.kind == "hat" and 1 <= event.value <= 0x0F:
            return cls("hat", event.index, event.value)
        return None

    def to_config(self) -> dict[str, int | str]:
        return {"kind": self.kind, "index": self.index, "value": self.value}

    def matches(self, event: ControllerEvent) -> bool:
        """Whether ``event`` is this binding's discrete activation edge."""
        if event.initial or event.kind != self.kind or event.index != self.index:
            return False
        if self.kind == "button":
            return event.value != 0
        return event.value == self.value

    def describe(self) -> str:
        if self.kind == "button":
            return f"Button {self.index}"
        return f"Hat {self.index} {hat_direction(self.value)}"


def parse_bindings(raw: object) -> dict[str, ControllerBinding]:
    """Return valid, known bindings from an untrusted config value."""
    if not isinstance(raw, Mapping):
        return {}
    bindings: dict[str, ControllerBinding] = {}
    for action_id, _label in CONTROLLER_ACTIONS:
        binding = ControllerBinding.from_config(raw.get(action_id))
        if binding is not None and binding not in bindings.values():
            bindings[action_id] = binding
    return bindings


def serialize_bindings(
    bindings: Mapping[str, ControllerBinding],
) -> dict[str, dict[str, int | str]]:
    """Produce the compact JSON-safe binding mapping stored in ``Config``."""
    return {
        action_id: bindings[action_id].to_config()
        for action_id, _label in CONTROLLER_ACTIONS
        if action_id in bindings
    }


def assign_binding(
    bindings: dict[str, ControllerBinding],
    action_id: str,
    binding: ControllerBinding,
) -> None:
    """Assign uniquely: one physical control can invoke only one action."""
    if action_id not in CONTROLLER_ACTION_IDS:
        raise KeyError(action_id)
    for existing_action, existing in tuple(bindings.items()):
        if existing == binding:
            bindings.pop(existing_action)
    bindings[action_id] = binding


def action_for_event(
    raw_bindings: object,
    event: ControllerEvent,
) -> str | None:
    """Find the configured action activated by ``event``, if any."""
    for action_id, binding in parse_bindings(raw_bindings).items():
        if binding.matches(event):
            return action_id
    return None
