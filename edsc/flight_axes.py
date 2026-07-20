"""Join Elite's flight bindings to live controllers and track their state; ``binds.py`` reports what a preset says, this turns that into live axis indices and, from the raw event stream, the six-degree-of-freedom input the game receives. Nothing here reads or writes Frontier's files."""

# SPDX-License-Identifier: GPL-3.0-or-later

from __future__ import annotations

import math
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field

from .binds import FlightBinds, resolve_device
from .platform.controller import ControllerDevice, ControllerEvent, normalize_axis

# Elite's axis names, mapped to the kernel ABS codes a joystick reports; Joy_UAxis/Joy_VAxis are DirectInput sliders with no settled ABS equivalent, so they stay unresolved rather than guessed at.
ED_AXIS_ABS: dict[str, int] = {
    "Joy_XAxis": 0,  # ABS_X
    "Joy_YAxis": 1,  # ABS_Y
    "Joy_ZAxis": 2,  # ABS_Z
    "Joy_RXAxis": 3,  # ABS_RX
    "Joy_RYAxis": 4,  # ABS_RY
    "Joy_RZAxis": 5,  # ABS_RZ
}

# Elite numbers joystick buttons from 1; no preset it ships ever says Joy_0.
_BUTTON_RE = re.compile(r"^Joy_(\d+)$")

ROTATION_AXES = ("roll", "pitch", "yaw")
TRANSLATION_AXES = ("lateral", "vertical", "throttle")
# Every axis a mapping may carry, in a fixed order so saved config is stable.
AXIS_NAMES = (*ROTATION_AXES, *TRANSLATION_AXES, "ahead")


def button_index(key: str) -> int | None:
    """Zero-based index for an Elite button name, or ``None`` if not a button; hats (``Joy_POV1Up``) and other non-numeric names resolve to nothing."""
    match = _BUTTON_RE.match(key)
    if match is None:
        return None
    number = int(match.group(1))
    return number - 1 if number >= 1 else None


def apply_deadzone(value: float, deadzone: float) -> float:
    """Zero ``value`` inside ``deadzone``, rescaling the rest to the full range."""
    if deadzone <= 0.0:
        return value
    if deadzone >= 1.0:
        return 0.0
    magnitude = abs(value)
    if magnitude <= deadzone:
        return 0.0
    return math.copysign((magnitude - deadzone) / (1.0 - deadzone), value)


def device_vid_pid(device: ControllerDevice) -> str:
    """The ``VVVVPPPP`` id a binds file would use for ``device``."""
    return f"{device.vendor_id:04X}{device.product_id:04X}"


def _index_of(raw: object) -> int | None:
    """A non-negative control index from untrusted JSON, or ``None``."""
    if isinstance(raw, bool) or not isinstance(raw, int) or raw < 0:
        return None
    return raw


@dataclass(frozen=True, slots=True)
class ResolvedAxis:
    """One flight axis pinned to a live controller axis."""

    device_id: str
    index: int
    inverted: bool = False
    deadzone: float = 0.0

    @classmethod
    def from_config(cls, raw: object) -> ResolvedAxis | None:
        if not isinstance(raw, Mapping):
            return None
        device = raw.get("device")
        index = _index_of(raw.get("index"))
        if not isinstance(device, str) or not device or index is None:
            return None
        try:
            deadzone = abs(float(raw.get("deadzone", 0.0)))
        except (TypeError, ValueError):
            deadzone = 0.0
        return cls(device, index, bool(raw.get("inverted")), min(deadzone, 1.0))

    def to_config(self) -> dict[str, object]:
        return {
            "device": self.device_id,
            "index": self.index,
            "inverted": self.inverted,
            "deadzone": self.deadzone,
        }


@dataclass(frozen=True, slots=True)
class ResolvedButton:
    """One flight button pinned to a live controller button."""

    device_id: str
    index: int

    @classmethod
    def from_config(cls, raw: object) -> ResolvedButton | None:
        if not isinstance(raw, Mapping):
            return None
        device = raw.get("device")
        index = _index_of(raw.get("index"))
        if not isinstance(device, str) or not device or index is None:
            return None
        return cls(device, index)

    def to_config(self) -> dict[str, object]:
        return {"device": self.device_id, "index": self.index}


@dataclass(frozen=True, slots=True)
class FlightMapping:
    """Live bindings for the gizmos, plus what could not be resolved."""

    axes: dict[str, ResolvedAxis] = field(default_factory=dict)
    reverse: ResolvedButton | None = None
    reverse_is_hold: bool = False
    reverse_unobservable: bool = False
    throttle_forward_only: bool = False
    speed_presets: tuple[tuple[float, ResolvedButton], ...] = ()
    boost: ResolvedButton | None = None
    # Human-readable reasons a binding didn't resolve, for the settings UI to show instead of silently dropping it.
    unresolved: tuple[str, ...] = ()

    @property
    def device_ids(self) -> tuple[str, ...]:
        """Every live device this mapping needs, in first-seen order."""
        seen: list[str] = []
        for item in (
            *self.axes.values(),
            *(b for _value, b in self.speed_presets),
            *((self.reverse,) if self.reverse is not None else ()),
            *((self.boost,) if self.boost is not None else ()),
        ):
            if item.device_id not in seen:
                seen.append(item.device_id)
        return tuple(seen)

    @property
    def is_empty(self) -> bool:
        return not self.axes

    @classmethod
    def from_config(cls, raw: object) -> FlightMapping:
        """Rebuild a saved mapping, dropping anything that no longer parses; ``unresolved`` isn't persisted (it describes one moment's hardware and is recomputed whenever we resolve against live devices)."""
        if not isinstance(raw, Mapping):
            return cls()
        axes_raw = raw.get("axes")
        axes: dict[str, ResolvedAxis] = {}
        if isinstance(axes_raw, Mapping):
            for name in AXIS_NAMES:
                axis = ResolvedAxis.from_config(axes_raw.get(name))
                if axis is not None:
                    axes[name] = axis
        presets: list[tuple[float, ResolvedButton]] = []
        raw_presets = raw.get("speed_presets")
        if isinstance(raw_presets, Sequence) and not isinstance(raw_presets, str):
            for entry in raw_presets:
                if not isinstance(entry, Sequence) or len(entry) != 2:
                    continue
                try:
                    value = float(entry[0])
                except (TypeError, ValueError):
                    continue
                button = ResolvedButton.from_config(entry[1])
                if button is not None and -1.0 <= value <= 1.0:
                    presets.append((value, button))
        return cls(
            axes=axes,
            reverse=ResolvedButton.from_config(raw.get("reverse")),
            reverse_is_hold=bool(raw.get("reverse_is_hold")),
            reverse_unobservable=bool(raw.get("reverse_unobservable")),
            throttle_forward_only=bool(raw.get("throttle_forward_only")),
            speed_presets=tuple(presets),
            boost=ResolvedButton.from_config(raw.get("boost")),
        )

    def to_config(self) -> dict[str, object]:
        """A JSON-safe snapshot for ``Config.flight_mapping``."""
        return {
            "axes": {name: axis.to_config() for name, axis in self.axes.items()},
            "reverse": self.reverse.to_config() if self.reverse else None,
            "reverse_is_hold": self.reverse_is_hold,
            "reverse_unobservable": self.reverse_unobservable,
            "throttle_forward_only": self.throttle_forward_only,
            "speed_presets": [
                [value, button.to_config()] for value, button in self.speed_presets
            ],
            "boost": self.boost.to_config() if self.boost else None,
        }


@dataclass(frozen=True, slots=True)
class FlightState:
    """Input the game is receiving, each axis in ``-1..1``."""

    roll: float = 0.0
    pitch: float = 0.0
    yaw: float = 0.0
    lateral: float = 0.0
    vertical: float = 0.0
    throttle: float = 0.0
    # True/False while reverse is knowable, ``None`` when the throttle is natively bidirectional (no reverse concept) or reverse is bound somewhere we can't watch.
    reverse: bool | None = None


def _match_device(
    reference: str,
    devices: Sequence[ControllerDevice],
    mappings: dict[str, tuple[str, ...]],
) -> tuple[ControllerDevice | None, str]:
    """Find the one live device a binds ``Device`` value names; returns the device and an empty reason, or ``None`` and why not. Identical hardware is reported not guessed at -- a binds file identifies devices only by vendor+product, which can't tell two same-model sticks apart."""
    ids = resolve_device(reference, mappings)
    if not ids:
        return None, f"{reference} is not a joystick we can identify"
    matches = [d for d in devices if device_vid_pid(d) in ids]
    if not matches:
        return None, f"{reference} is not connected"
    if len(matches) > 1:
        return None, f"{reference} matches {len(matches)} identical devices"
    return matches[0], ""


def _axis_index(device: ControllerDevice, key: str) -> tuple[int | None, str]:
    """Live axis index for an Elite axis name on ``device``."""
    code = ED_AXIS_ABS.get(key)
    if code is None:
        return None, f"{key} has no kernel axis equivalent"
    if not device.axis_codes:
        # SDL2 cannot report an axis map, so Windows resolves by hand.
        return None, f"{device.name} cannot report which axis is which"
    try:
        return device.axis_codes.index(code), ""
    except ValueError:
        return None, f"{device.name} has no {key}"


def resolve_mapping(
    binds: FlightBinds,
    devices: Sequence[ControllerDevice],
    device_mappings: dict[str, tuple[str, ...]] | None = None,
) -> FlightMapping:
    """Resolve one preset against the controllers currently connected."""
    mappings = device_mappings or {}
    axes: dict[str, ResolvedAxis] = {}
    unresolved: list[str] = []

    for name, binding in binds.axes.items():
        device, reason = _match_device(binding.device, devices, mappings)
        if device is None:
            unresolved.append(f"{name}: {reason}")
            continue
        index, reason = _axis_index(device, binding.key)
        if index is None:
            unresolved.append(f"{name}: {reason}")
            continue
        axes[name] = ResolvedAxis(
            device.id, index, binding.inverted, binding.deadzone
        )

    reverse: ResolvedButton | None = None
    if binds.reverse is not None:
        device, reason = _match_device(binds.reverse.device, devices, mappings)
        index = button_index(binds.reverse.key) if device is not None else None
        if device is None:
            unresolved.append(f"reverse: {reason}")
        elif index is None:
            unresolved.append(f"reverse: {binds.reverse.key} is not a button")
        else:
            reverse = ResolvedButton(device.id, index)

    presets: list[tuple[float, ResolvedButton]] = []
    for value, binding in binds.speed_presets:
        device, reason = _match_device(binding.device, devices, mappings)
        index = button_index(binding.key) if device is not None else None
        if device is None:
            unresolved.append(f"speed {value:+.0%}: {reason}")
        elif index is None:
            unresolved.append(f"speed {value:+.0%}: {binding.key} is not a button")
        else:
            presets.append((value, ResolvedButton(device.id, index)))

    boost: ResolvedButton | None = None
    if binds.boost is not None:
        device, reason = _match_device(binds.boost.device, devices, mappings)
        index = button_index(binds.boost.key) if device is not None else None
        if device is None:
            unresolved.append(f"boost: {reason}")
        elif index is None:
            unresolved.append(f"boost: {binds.boost.key} is not a button")
        else:
            boost = ResolvedButton(device.id, index)

    return FlightMapping(
        axes=axes,
        reverse=reverse,
        reverse_is_hold=binds.reverse_is_hold,
        reverse_unobservable=binds.reverse_unobservable or (
            binds.reverse is not None and reverse is None
        ),
        throttle_forward_only=binds.throttle_forward_only,
        speed_presets=tuple(presets),
        boost=boost,
        unresolved=tuple(unresolved),
    )


class FlightTracker:
    """Fold raw controller events into the input the game is receiving; the throttle follows what the game got -- an absolute preset holds until the axis next moves, matching Elite's last-writer-wins behaviour."""

    def __init__(
        self, mapping: FlightMapping, *, apply_deadzones: bool = True
    ) -> None:
        self.mapping = mapping
        self.apply_deadzones = apply_deadzones
        self._axis_values: dict[tuple[str, int], int] = {}
        self._buttons: dict[tuple[str, int], bool] = {}
        # Toggled reverse can't be read back from the game, so assume the session starts pointing forward and resync on any signed preset.
        self._reverse_toggle = False
        self._throttle_preset: float | None = None

    def handle(self, event: ControllerEvent) -> bool:
        """Fold one event in. Returns whether it could have changed the state."""
        if event.kind == "axis":
            return self._handle_axis(event)
        if event.kind == "button":
            return self._handle_button(event)
        return False

    def _handle_axis(self, event: ControllerEvent) -> bool:
        key = (event.device_id, event.index)
        if self._axis_values.get(key) == event.value:
            return False
        self._axis_values[key] = event.value
        throttle = self.mapping.axes.get("throttle")
        # Moving the throttle lever takes authority back from a preset.
        if (
            not event.initial
            and throttle is not None
            and (throttle.device_id, throttle.index) == key
        ):
            self._throttle_preset = None
        return self._is_mapped_axis(key)

    def _handle_button(self, event: ControllerEvent) -> bool:
        key = (event.device_id, event.index)
        pressed = event.value != 0
        was_pressed = self._buttons.get(key, False)
        self._buttons[key] = pressed
        if event.initial or not pressed or was_pressed:
            # Only a fresh press acts; initial reports just seed held state, which is all hold-to-reverse needs.
            return self._is_reverse(key)

        changed = False
        if self._is_reverse(key):
            if not self.mapping.reverse_is_hold:
                self._reverse_toggle = not self._reverse_toggle
            changed = True
        for value, button in self.mapping.speed_presets:
            if (button.device_id, button.index) == key:
                self._throttle_preset = value
                # A signed preset states the direction outright, the only way a toggled reverse ever gets back in sync.
                if value != 0.0:
                    self._reverse_toggle = value < 0.0
                changed = True
        return changed

    def _is_reverse(self, key: tuple[str, int]) -> bool:
        reverse = self.mapping.reverse
        return reverse is not None and (reverse.device_id, reverse.index) == key

    def _is_mapped_axis(self, key: tuple[str, int]) -> bool:
        return any(
            (axis.device_id, axis.index) == key for axis in self.mapping.axes.values()
        )

    def _axis(self, name: str) -> float:
        """One mapped axis in ``-1..1``, inverted and deadzoned as Elite would."""
        axis = self.mapping.axes.get(name)
        if axis is None:
            return 0.0
        raw = self._axis_values.get((axis.device_id, axis.index), 0)
        value = normalize_axis(raw)
        if axis.inverted:
            value = -value
        if self.apply_deadzones:
            value = apply_deadzone(value, axis.deadzone)
        return max(-1.0, min(1.0, value))

    def _reverse(self) -> bool | None:
        if self.mapping.reverse is not None:
            if self.mapping.reverse_is_hold:
                key = (self.mapping.reverse.device_id, self.mapping.reverse.index)
                return self._buttons.get(key, False)
            return self._reverse_toggle
        if self.mapping.reverse_unobservable:
            return None
        # Nothing bound: this pilot has no way to reverse at all.
        return False

    def _throttle(self) -> tuple[float, bool | None]:
        # A bound AheadThrust axis is bidirectional in its own right, so reverse never enters into it.
        if "ahead" in self.mapping.axes:
            return self._axis("ahead"), None
        if "throttle" not in self.mapping.axes:
            return (self._throttle_preset or 0.0), self._reverse()
        if not self.mapping.throttle_forward_only:
            if self._throttle_preset is not None:
                return self._throttle_preset, None
            return self._axis("throttle"), None

        reverse = self._reverse()
        if self._throttle_preset is not None:
            return self._throttle_preset, reverse
        # Forward-only maps the lever's whole travel onto 0..100%: fully back is a stop, not full reverse.
        magnitude = (self._axis("throttle") + 1.0) / 2.0
        return (-magnitude if reverse else magnitude), reverse

    @property
    def state(self) -> FlightState:
        """The current six-degree-of-freedom input."""
        throttle, reverse = self._throttle()
        return FlightState(
            roll=self._axis("roll"),
            pitch=self._axis("pitch"),
            yaw=self._axis("yaw"),
            lateral=self._axis("lateral"),
            vertical=self._axis("vertical"),
            throttle=throttle,
            reverse=reverse,
        )

    def rebind(self, mapping: FlightMapping) -> None:
        """Swap in a new mapping, keeping raw values already observed."""
        self.mapping = mapping
        self._throttle_preset = None

    def reset(self) -> None:
        """Forget observed state (a device went away, or the session restarted)."""
        self._axis_values.clear()
        self._buttons.clear()
        self._reverse_toggle = False
        self._throttle_preset = None
