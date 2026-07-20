"""Shared controller model and platform-backend facade."""

# SPDX-License-Identifier: GPL-3.0-or-later

from __future__ import annotations

import sys
from collections.abc import Callable
from dataclasses import dataclass
from typing import Literal, Protocol

from PySide6.QtCore import QObject, Signal

ControllerKind = Literal["axis", "button", "hat", "ball_x", "ball_y"]
_HAT_DIRECTIONS = (
    (0x01, "Up"),
    (0x02, "Right"),
    (0x04, "Down"),
    (0x08, "Left"),
)


def hat_direction(value: int) -> str:
    directions = [name for mask, name in _HAT_DIRECTIONS if value & mask]
    return " + ".join(directions) if directions else "Centered"


def normalize_axis(value: int) -> float:
    divisor = 32768.0 if value < 0 else 32767.0
    return max(-1.0, min(1.0, value / divisor))


@dataclass(frozen=True, slots=True)
class ControllerDevice:
    """Identity and input capabilities for one open controller."""

    id: str
    name: str
    backend: str
    vendor_id: int = 0
    product_id: int = 0
    serial: str = ""
    path: str = ""
    axes: int = 0
    # Kernel ABS code per axis index, where the backend can report it; joystick APIs number axes densely over whatever a device declares, so an index alone doesn't say *which* axis it is -- this maps index -> ABS_X, ABS_RZ, etc. Empty when the backend can't say (SDL2 has no equivalent).
    axis_codes: tuple[int, ...] = ()
    buttons: int = 0
    hats: int = 0
    balls: int = 0


@dataclass(frozen=True, slots=True)
class ControllerEvent:
    """One raw controller value change; axis values keep the kernel/SDL signed 16-bit range (``-32768..32767``), buttons are ``0``/``1``, and hats use SDL's directional bit mask. Initial state reports are marked so the binding recorder ignores controls already held when a device is opened."""

    device_id: str
    kind: ControllerKind
    index: int
    value: int
    timestamp_ms: int = 0
    initial: bool = False

    @property
    def pressed(self) -> bool:
        return self.kind == "button" and self.value != 0

    @property
    def normalized_axis(self) -> float | None:
        """Axis value in ``[-1, 1]``, or ``None`` for a non-axis event."""
        if self.kind != "axis":
            return None
        return normalize_axis(self.value)


DeviceCallback = Callable[[ControllerDevice], None]
EventCallback = Callable[[ControllerEvent], None]
DisconnectCallback = Callable[[str], None]
ErrorCallback = Callable[[str], None]


class ControllerBackend(Protocol):
    name: str
    available: bool

    def start(self) -> bool: ...

    def rescan(self) -> None: ...

    def stop(self) -> None: ...


BackendFactory = Callable[
    [DeviceCallback, DisconnectCallback, EventCallback, ErrorCallback, QObject],
    ControllerBackend,
]


class ControllerMonitor(QObject):
    """Process-wide controller event source; signals are delivered on the GUI thread. Capture is intentionally not gated on Elite having focus -- joystick APIs are non-exclusive, and the binding layer decides when an event should invoke an action."""

    device_connected = Signal(object)  # ControllerDevice
    device_disconnected = Signal(str)  # ControllerDevice.id
    event_received = Signal(object)  # ControllerEvent
    error = Signal(str)

    def __init__(
        self,
        parent: QObject | None = None,
        *,
        backend_factory: BackendFactory | None = None,
    ) -> None:
        super().__init__(parent)
        factory = backend_factory or make_controller_backend
        self._devices: dict[str, ControllerDevice] = {}
        self._values: dict[str, dict[tuple[ControllerKind, int], int]] = {}
        self._backend = factory(
            self._on_device_connected,
            self._on_device_disconnected,
            self._on_event,
            self._on_error,
            self,
        )
        self.available = False
        self.last_error = ""
        self._started = False

    @property
    def backend_name(self) -> str:
        return self._backend.name

    @property
    def devices(self) -> tuple[ControllerDevice, ...]:
        """Currently open devices, in deterministic id order."""
        return tuple(self._devices[key] for key in sorted(self._devices))

    def values(self, device_id: str) -> dict[tuple[ControllerKind, int], int]:
        """Snapshot of the latest raw value for each control on one device."""
        return dict(self._values.get(device_id, {}))

    def start(self) -> bool:
        """Start capture. Returns whether this platform backend is available."""
        if self._started:
            return self.available
        self._started = True
        self.last_error = ""
        self.available = bool(self._backend.start())
        return self.available

    def rescan(self) -> bool:
        """Retry capture if needed, otherwise ask the backend to discover now."""
        if not self._started or not self.available:
            if self._started:
                self._backend.stop()
                self._started = False
            return self.start()
        self.last_error = ""
        self._backend.rescan()
        self.available = bool(self._backend.available)
        return self.available

    def stop(self) -> None:
        if not self._started:
            return
        self._started = False
        self._backend.stop()
        self._devices.clear()
        self._values.clear()
        self.available = False

    def _on_device_connected(self, device: ControllerDevice) -> None:
        self.last_error = ""
        self._devices[device.id] = device
        self._values[device.id] = {}
        self.device_connected.emit(device)

    def _on_device_disconnected(self, device_id: str) -> None:
        self._devices.pop(device_id, None)
        self._values.pop(device_id, None)
        self.device_disconnected.emit(device_id)

    def _on_event(self, event: ControllerEvent) -> None:
        if event.device_id not in self._devices:
            return
        # Track absolute controls; trackball values are relative deltas and don't represent a persistent state.
        if event.kind not in ("ball_x", "ball_y"):
            self._values[event.device_id][(event.kind, event.index)] = event.value
        self.event_received.emit(event)

    def _on_error(self, message: str) -> None:
        self.last_error = message
        self.error.emit(message)


def make_controller_backend(
    on_device: DeviceCallback,
    on_disconnect: DisconnectCallback,
    on_event: EventCallback,
    on_error: ErrorCallback,
    parent: QObject,
) -> ControllerBackend:
    """Construct the native backend for the current OS."""
    if sys.platform == "win32":
        from .controller_sdl2 import Sdl2ControllerBackend

        return Sdl2ControllerBackend(
            on_device, on_disconnect, on_event, on_error, parent
        )
    if sys.platform.startswith("linux"):
        from .controller_linux import LinuxControllerBackend

        return LinuxControllerBackend(
            on_device, on_disconnect, on_event, on_error, parent
        )
    return NullControllerBackend(parent)


class NullControllerBackend(QObject):
    """Unsupported-platform backend."""

    name = "unavailable"
    available = False

    def start(self) -> bool:
        return False

    def rescan(self) -> None:
        pass

    def stop(self) -> None:
        pass
