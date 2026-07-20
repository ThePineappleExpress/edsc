"""Capture VKB controllers globally on Windows through SDL2."""

# SPDX-License-Identifier: GPL-3.0-or-later

from __future__ import annotations

import ctypes
import hashlib
import importlib
from collections.abc import Callable
from contextlib import suppress
from typing import Any

from PySide6.QtCore import QObject, Qt, QTimer

from .controller import ControllerDevice, ControllerEvent

# USB VID used by VKB-Sim products (registered as Fervian Technologies).
VKB_VENDOR_ID = 0x231D
_POLL_INTERVAL_MS = 8
_MAX_EVENTS_PER_TICK = 2048
_BACKGROUND_HINT = b"SDL_JOYSTICK_ALLOW_BACKGROUND_EVENTS"


def _text(value: object) -> str:
    if isinstance(value, bytes):
        return value.decode("utf-8", "replace").strip()
    if isinstance(value, str):
        return value.strip()
    return ""


def is_vkb_device(vendor_id: int, name: str) -> bool:
    """True for an identified VKB controller; the VID is authoritative, and the name fallback keeps older SDL/DirectInput stacks useful when they can't expose a USB VID."""
    return vendor_id == VKB_VENDOR_ID or (
        vendor_id == 0 and name.strip().upper().startswith("VKB")
    )


class Sdl2ControllerBackend(QObject):
    """Pump SDL's global joystick queue from the Qt event loop."""

    name = "sdl2-vkb"

    def __init__(
        self,
        on_device: Callable[[ControllerDevice], None],
        on_disconnect: Callable[[str], None],
        on_event: Callable[[ControllerEvent], None],
        on_error: Callable[[str], None],
        parent: QObject,
        *,
        sdl: Any = None,
    ) -> None:
        super().__init__(parent)
        self.available = False
        self._on_device = on_device
        self._on_disconnect = on_disconnect
        self._on_event = on_event
        self._on_error = on_error
        self._sdl = sdl
        self._initialized = False
        self._joysticks: dict[int, object] = {}
        self._instance_to_id: dict[int, str] = {}
        self._devices: dict[str, ControllerDevice] = {}
        self._timer = QTimer(self)
        self._timer.setTimerType(Qt.PreciseTimer)
        self._timer.setInterval(_POLL_INTERVAL_MS)
        self._timer.timeout.connect(self._poll)

    def start(self) -> bool:
        if self.available:
            return True
        if self._sdl is None:
            try:
                sdl2 = importlib.import_module("sdl2")
            except Exception as exc:
                self._on_error(f"SDL2 controller support is unavailable: {exc}")
                return False
            self._sdl = sdl2

        sdl = self._sdl
        try:
            if not sdl.SDL_SetHintWithPriority(
                _BACKGROUND_HINT, b"1", sdl.SDL_HINT_OVERRIDE
            ):
                self._on_error("SDL2 refused to enable background controller events")
                return False
            flags = sdl.SDL_INIT_EVENTS | sdl.SDL_INIT_JOYSTICK
            if sdl.SDL_InitSubSystem(flags) != 0:
                self._on_error(f"SDL2 controller initialization failed: {self._error()}")
                return False
            self._initialized = True
            sdl.SDL_JoystickEventState(sdl.SDL_ENABLE)
            self.available = True
            for index in range(max(0, int(sdl.SDL_NumJoysticks()))):
                if not self.available:
                    return False
                self._open_device(index)
            if self.available:
                self._timer.start()
            return self.available
        except Exception as exc:
            self._on_error(f"SDL2 controller initialization failed: {exc}")
            self._shutdown(notify=True)
            return False

    def _open_device(self, index: int) -> None:
        sdl = self._sdl
        name = _text(sdl.SDL_JoystickNameForIndex(index)) or f"Controller {index}"
        vendor = int(sdl.SDL_JoystickGetDeviceVendor(index))
        if not is_vkb_device(vendor, name):
            return

        handle = sdl.SDL_JoystickOpen(index)
        if not handle:
            self._on_error(
                f"Cannot open VKB controller {name}: {self._error()}"
            )
            return
        try:
            opened_name = _text(sdl.SDL_JoystickName(handle))
            opened_vendor = int(sdl.SDL_JoystickGetVendor(handle))
            if not is_vkb_device(opened_vendor, opened_name):
                sdl.SDL_JoystickClose(handle)
                return
            name = opened_name or name
            vendor = opened_vendor
            instance_id = int(sdl.SDL_JoystickInstanceID(handle))
            if instance_id < 0:
                raise RuntimeError(self._error())
            if instance_id in self._joysticks:
                sdl.SDL_JoystickClose(handle)
                return

            product = int(sdl.SDL_JoystickGetProduct(handle))
            serial = self._optional_text("SDL_JoystickGetSerial", handle)
            path = self._optional_text("SDL_JoystickPath", handle)
            guid = self._guid(handle)
            identity_source = "\0".join(part for part in (serial, path) if part)
            identity = (
                hashlib.sha256(identity_source.encode("utf-8")).hexdigest()[:12]
                if identity_source
                else "device"
            )
            device_id = f"sdl2:{guid}:{identity}"
            if device_id in self._devices:
                device_id = f"{device_id}:{instance_id}"

            device = ControllerDevice(
                id=device_id,
                name=name,
                backend=self.name,
                vendor_id=vendor,
                product_id=product,
                serial=serial,
                path=path,
                axes=max(0, int(sdl.SDL_JoystickNumAxes(handle))),
                buttons=max(0, int(sdl.SDL_JoystickNumButtons(handle))),
                hats=max(0, int(sdl.SDL_JoystickNumHats(handle))),
                balls=max(0, int(sdl.SDL_JoystickNumBalls(handle))),
            )
            initial_events = self._initial_state(device, handle)
            self._joysticks[instance_id] = handle
            self._instance_to_id[instance_id] = device_id
            self._devices[device_id] = device
            self._on_device(device)
            for event in initial_events:
                self._on_event(event)
        except Exception as exc:
            sdl.SDL_JoystickClose(handle)
            self._on_error(f"Cannot inspect VKB controller {name}: {exc}")

    def _initial_state(
        self, device: ControllerDevice, handle: object
    ) -> list[ControllerEvent]:
        sdl = self._sdl
        sdl.SDL_JoystickUpdate()
        events: list[ControllerEvent] = []
        for index in range(device.axes):
            events.append(
                ControllerEvent(
                    device.id,
                    "axis",
                    index,
                    int(sdl.SDL_JoystickGetAxis(handle, index)),
                    initial=True,
                )
            )
        for index in range(device.buttons):
            events.append(
                ControllerEvent(
                    device.id,
                    "button",
                    index,
                    int(sdl.SDL_JoystickGetButton(handle, index)),
                    initial=True,
                )
            )
        for index in range(device.hats):
            events.append(
                ControllerEvent(
                    device.id,
                    "hat",
                    index,
                    int(sdl.SDL_JoystickGetHat(handle, index)),
                    initial=True,
                )
            )
        return events

    def _optional_text(self, function: str, handle: object) -> str:
        getter = getattr(self._sdl, function, None)
        if getter is None:
            return ""
        try:
            return _text(getter(handle))
        except (RuntimeError, TypeError, ValueError):
            return ""

    def _guid(self, handle: object) -> str:
        sdl = self._sdl
        guid = sdl.SDL_JoystickGetGUID(handle)
        # Test doubles may provide the canonical string directly.
        direct = _text(guid)
        if direct:
            return direct.lower()
        buffer = ctypes.create_string_buffer(33)
        sdl.SDL_JoystickGetGUIDString(guid, buffer, len(buffer))
        return _text(buffer.value).lower() or "unknown"

    def _poll(self) -> None:
        if not self.available:
            return
        sdl = self._sdl
        event = sdl.SDL_Event()
        count = 0
        while count < _MAX_EVENTS_PER_TICK and sdl.SDL_PollEvent(
            ctypes.byref(event)
        ):
            count += 1
            self._dispatch(event)

    def rescan(self) -> None:
        """Pump hotplug events and retry every currently enumerated device."""
        if not self.available:
            return
        try:
            for index in range(max(0, int(self._sdl.SDL_NumJoysticks()))):
                if not self.available:
                    return
                self._open_device(index)
        except Exception as exc:
            self._on_error(f"SDL2 controller rescan failed: {exc}")
        self._poll()

    def _dispatch(self, event: object) -> None:
        sdl = self._sdl
        event_type = int(event.type)
        if event_type == sdl.SDL_JOYDEVICEADDED:
            # Added events carry a device index, not an instance id.
            self._open_device(int(event.jdevice.which))
            return
        if event_type == sdl.SDL_JOYDEVICEREMOVED:
            self._remove_instance(int(event.jdevice.which), notify=True)
            return

        if event_type == sdl.SDL_JOYAXISMOTION:
            raw, kind, index, value = (
                event.jaxis,
                "axis",
                event.jaxis.axis,
                event.jaxis.value,
            )
        elif event_type in (sdl.SDL_JOYBUTTONDOWN, sdl.SDL_JOYBUTTONUP):
            raw, kind, index, value = (
                event.jbutton,
                "button",
                event.jbutton.button,
                event.jbutton.state,
            )
        elif event_type == sdl.SDL_JOYHATMOTION:
            raw, kind, index, value = (
                event.jhat,
                "hat",
                event.jhat.hat,
                event.jhat.value,
            )
        elif event_type == sdl.SDL_JOYBALLMOTION:
            raw = event.jball
            device_id = self._instance_to_id.get(int(raw.which))
            if device_id is None:
                return
            for kind, value in (("ball_x", raw.xrel), ("ball_y", raw.yrel)):
                if value:
                    self._on_event(
                        ControllerEvent(
                            device_id,
                            kind,
                            int(raw.ball),
                            int(value),
                            int(raw.timestamp),
                        )
                    )
            return
        else:
            return

        device_id = self._instance_to_id.get(int(raw.which))
        if device_id is None:
            return
        self._on_event(
            ControllerEvent(
                device_id,
                kind,
                int(index),
                int(value),
                int(raw.timestamp),
            )
        )

    def _remove_instance(self, instance_id: int, *, notify: bool) -> None:
        handle = self._joysticks.pop(instance_id, None)
        device_id = self._instance_to_id.pop(instance_id, None)
        if handle is not None:
            with suppress(Exception):
                self._sdl.SDL_JoystickClose(handle)
        if device_id is not None:
            self._devices.pop(device_id, None)
            if notify:
                self._on_disconnect(device_id)

    def _error(self) -> str:
        try:
            return _text(self._sdl.SDL_GetError()) or "unknown SDL error"
        except Exception:
            return "unknown SDL error"

    def _shutdown(self, *, notify: bool) -> None:
        self._timer.stop()
        for instance_id in list(self._joysticks):
            self._remove_instance(instance_id, notify=notify)
        if self._initialized:
            try:
                flags = self._sdl.SDL_INIT_EVENTS | self._sdl.SDL_INIT_JOYSTICK
                self._sdl.SDL_QuitSubSystem(flags)
            except Exception:
                pass
        self._initialized = False
        self.available = False

    def stop(self) -> None:
        self._shutdown(notify=False)
