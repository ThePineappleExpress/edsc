"""Capture Linux controllers through the native joystick API."""

# SPDX-License-Identifier: GPL-3.0-or-later

from __future__ import annotations

import errno
import fcntl
import hashlib
import os
import struct
from collections.abc import Callable
from contextlib import suppress
from pathlib import Path

from PySide6.QtCore import QObject, QSocketNotifier, QTimer

from .controller import ControllerDevice, ControllerEvent

_JS_EVENT = struct.Struct("=IhBB")
_JS_EVENT_BUTTON = 0x01
_JS_EVENT_AXIS = 0x02
_JS_EVENT_INIT = 0x80

_JSIOCGAXES = 0x80016A11
_JSIOCGBUTTONS = 0x80016A12
_JSIOCGNAME_BASE = 0x80006A13
# JSIOCGAXMAP reports the ABS code behind each dense axis index, in a buffer of ABS_CNT bytes; without it an index is ambiguous -- a stick declaring only X/Y/RZ reports RZ as index 2, not 5.
_JSIOCGAXMAP = 0x80406A32
_ABS_CNT = 64
_NAME_BUFFER_SIZE = 256
_SCAN_INTERVAL_MS = 1000
_READ_BATCH_BYTES = _JS_EVENT.size * 64


def _jsiocgname(size: int) -> int:
    return _JSIOCGNAME_BASE | (size << 16)


def _decode_c_string(value: bytes | bytearray) -> str:
    return bytes(value).split(b"\0", 1)[0].decode("utf-8", "replace").strip()


def _read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="replace").strip()
    except OSError:
        return ""


def _read_hex(path: Path) -> int:
    try:
        return int(_read_text(path), 16)
    except ValueError:
        return 0


def _ioctl_u8(fd: int, request: int) -> int:
    data = bytearray(1)
    try:
        fcntl.ioctl(fd, request, data, True)
    except OSError:
        return 0
    return data[0]


def _ioctl_name(fd: int) -> str:
    data = bytearray(_NAME_BUFFER_SIZE)
    try:
        fcntl.ioctl(fd, _jsiocgname(len(data)), data, True)
    except OSError:
        return ""
    return _decode_c_string(data)


def _ioctl_axis_map(fd: int, count: int) -> tuple[int, ...]:
    """ABS code for each of the first ``count`` axes; ``()`` if unavailable."""
    if count <= 0:
        return ()
    data = bytearray(_ABS_CNT)
    try:
        fcntl.ioctl(fd, _JSIOCGAXMAP, data, True)
    except OSError:
        return ()
    return tuple(data[: min(count, _ABS_CNT)])


def _device_number(path: Path) -> tuple[int, str]:
    suffix = path.name.removeprefix("js")
    try:
        return int(suffix), path.name
    except ValueError:
        return 1 << 30, path.name


class LinuxJoystickDevice(QObject):
    """One non-blocking Linux joystick descriptor."""

    def __init__(
        self,
        path: Path,
        info: ControllerDevice,
        on_event: Callable[[ControllerEvent], None],
        on_disconnect: Callable[[str], None],
        parent: QObject,
        *,
        fd: int,
    ) -> None:
        super().__init__(parent)
        self.path = path
        self.info = info
        self._fd = fd
        self._on_event = on_event
        self._on_disconnect = on_disconnect
        self._buffer = bytearray()
        self._closed = False
        self._notifier = QSocketNotifier(fd, QSocketNotifier.Read, self)
        self._notifier.activated.connect(self._on_ready)

    @classmethod
    def open(
        cls,
        path: Path,
        on_event: Callable[[ControllerEvent], None],
        on_disconnect: Callable[[str], None],
        parent: QObject,
    ) -> LinuxJoystickDevice:
        fd = os.open(path, os.O_RDONLY | os.O_NONBLOCK | os.O_CLOEXEC)
        try:
            info = cls._describe(path, fd)
            return cls(path, info, on_event, on_disconnect, parent, fd=fd)
        except Exception:
            os.close(fd)
            raise

    @staticmethod
    def _describe(path: Path, fd: int) -> ControllerDevice:
        sys_device = Path("/sys/class/input") / path.name / "device"
        name = _ioctl_name(fd) or _read_text(sys_device / "name") or path.name
        vendor = _read_hex(sys_device / "id" / "vendor")
        product = _read_hex(sys_device / "id" / "product")
        serial = _read_text(sys_device / "uniq")
        physical_path = _read_text(sys_device / "phys")
        identity = "\0".join(part for part in (serial, physical_path) if part)
        identity = identity or path.name
        identity_hash = hashlib.sha256(identity.encode("utf-8")).hexdigest()[:12]
        axes = _ioctl_u8(fd, _JSIOCGAXES)
        buttons = _ioctl_u8(fd, _JSIOCGBUTTONS)
        return ControllerDevice(
            id=f"linux:{vendor:04x}:{product:04x}:{identity_hash}",
            name=name,
            backend="linux-js",
            vendor_id=vendor,
            product_id=product,
            serial=serial,
            path=str(path),
            axes=axes,
            axis_codes=_ioctl_axis_map(fd, axes),
            buttons=buttons,
        )

    def _on_ready(self, *_args) -> None:
        while not self._closed:
            try:
                chunk = os.read(self._fd, _READ_BATCH_BYTES)
            except BlockingIOError:
                return
            except OSError as exc:
                if exc.errno in (errno.EAGAIN, errno.EWOULDBLOCK):
                    return
                self.stop(notify=True)
                return
            if not chunk:
                self.stop(notify=True)
                return
            self._buffer.extend(chunk)
            self._drain_records()
            if len(chunk) < _READ_BATCH_BYTES:
                return

    def _drain_records(self) -> None:
        complete = len(self._buffer) - (len(self._buffer) % _JS_EVENT.size)
        for offset in range(0, complete, _JS_EVENT.size):
            timestamp, value, raw_type, number = _JS_EVENT.unpack_from(
                self._buffer, offset
            )
            kind = raw_type & ~_JS_EVENT_INIT
            if kind == _JS_EVENT_BUTTON:
                event_kind = "button"
            elif kind == _JS_EVENT_AXIS:
                event_kind = "axis"
            else:
                continue
            self._on_event(
                ControllerEvent(
                    device_id=self.info.id,
                    kind=event_kind,
                    index=number,
                    value=int(value),
                    timestamp_ms=timestamp,
                    initial=bool(raw_type & _JS_EVENT_INIT),
                )
            )
        if complete:
            del self._buffer[:complete]

    def stop(self, *, notify: bool = False) -> None:
        if self._closed:
            return
        self._closed = True
        self._notifier.setEnabled(False)
        self._notifier.deleteLater()
        with suppress(OSError):
            os.close(self._fd)
        if notify:
            self._on_disconnect(self.info.id)


class LinuxControllerBackend(QObject):
    """Hotplug-aware collection of native Linux joystick descriptors."""

    name = "linux-js"

    def __init__(
        self,
        on_device: Callable[[ControllerDevice], None],
        on_disconnect: Callable[[str], None],
        on_event: Callable[[ControllerEvent], None],
        on_error: Callable[[str], None],
        parent: QObject,
        *,
        device_dir: Path = Path("/dev/input"),
    ) -> None:
        super().__init__(parent)
        self.available = False
        self._on_device = on_device
        self._on_disconnect = on_disconnect
        self._on_event = on_event
        self._on_error = on_error
        self._device_dir = Path(device_dir)
        self._devices: dict[Path, LinuxJoystickDevice] = {}
        self._reported_errors: dict[Path, str] = {}
        self._timer = QTimer(self)
        self._timer.setInterval(_SCAN_INTERVAL_MS)
        self._timer.timeout.connect(self.scan)

    def start(self) -> bool:
        if self.available:
            return True
        self.available = True
        self.scan()
        if self.available:
            self._timer.start()
        return self.available

    def scan(self) -> None:
        """Discover newly created nodes and retire unplugged devices."""
        try:
            paths = set(self._device_dir.glob("js*"))
        except OSError:
            paths = set()

        for path, device in list(self._devices.items()):
            if path not in paths or device._closed:
                device.stop(notify=not device._closed)
                self._devices.pop(path, None)

        for path in sorted(paths - self._devices.keys(), key=_device_number):
            if not self.available:
                return
            try:
                device = LinuxJoystickDevice.open(
                    path,
                    self._on_event,
                    lambda device_id, p=path: self._device_closed(p, device_id),
                    self,
                )
            except OSError as exc:
                message = f"Cannot open controller {path}: {exc.strerror or exc}"
                if self._reported_errors.get(path) != message:
                    self._reported_errors[path] = message
                    self._on_error(message)
                continue
            self._reported_errors.pop(path, None)
            self._devices[path] = device
            self._on_device(device.info)

        for path in set(self._reported_errors) - paths:
            self._reported_errors.pop(path, None)

    def rescan(self) -> None:
        # A user-requested retry should surface an error again if the node is still unreadable; periodic scans remain deduplicated.
        self._reported_errors.clear()
        self.scan()

    def _device_closed(self, path: Path, device_id: str) -> None:
        self._devices.pop(path, None)
        self._on_disconnect(device_id)

    def stop(self) -> None:
        self._timer.stop()
        for device in list(self._devices.values()):
            device.stop()
        self._devices.clear()
        self._reported_errors.clear()
        self.available = False
