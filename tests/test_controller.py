import errno
import os
import struct
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import pytest

from edsc.platform.controller import (
    ControllerDevice,
    ControllerEvent,
    ControllerMonitor,
)
from edsc.platform.controller_sdl2 import (
    VKB_VENDOR_ID,
    Sdl2ControllerBackend,
    is_vkb_device,
)

_IS_LINUX = sys.platform.startswith("linux")
if _IS_LINUX:
    from edsc.platform import controller_linux
    from edsc.platform.controller_linux import (
        LinuxControllerBackend,
        LinuxJoystickDevice,
    )


class _FakeBackend:
    name = "fake"

    def __init__(self, on_device, on_disconnect, on_event, on_error):
        self.available = False
        self.on_device = on_device
        self.on_disconnect = on_disconnect
        self.on_event = on_event
        self.on_error = on_error
        self.starts = 0
        self.rescans = 0
        self.stops = 0

    def start(self):
        self.starts += 1
        self.available = True
        return True

    def stop(self):
        self.stops += 1
        self.available = False

    def rescan(self):
        self.rescans += 1


def test_monitor_tracks_devices_absolute_state_and_signals(qapp):
    created = []

    def factory(on_device, on_disconnect, on_event, on_error, _parent):
        backend = _FakeBackend(on_device, on_disconnect, on_event, on_error)
        created.append(backend)
        return backend

    monitor = ControllerMonitor(qapp, backend_factory=factory)
    connected = []
    disconnected = []
    events = []
    errors = []
    monitor.device_connected.connect(connected.append)
    monitor.device_disconnected.connect(disconnected.append)
    monitor.event_received.connect(events.append)
    monitor.error.connect(errors.append)

    assert monitor.backend_name == "fake"
    assert monitor.start() is True
    assert monitor.start() is True
    assert created[0].starts == 1

    device = ControllerDevice("stick-1", "VKB Gladiator", "fake", axes=1)
    created[0].on_event(ControllerEvent("unknown", "axis", 0, 10))
    created[0].on_device(device)
    axis = ControllerEvent("stick-1", "axis", 0, -16384)
    ball = ControllerEvent("stick-1", "ball_x", 0, 4)
    created[0].on_event(axis)
    created[0].on_event(ball)
    created[0].on_error("test error")

    assert connected == [device]
    assert monitor.devices == (device,)
    assert events == [axis, ball]
    assert monitor.values("stick-1") == {("axis", 0): -16384}
    assert errors == ["test error"]
    assert monitor.last_error == "test error"

    assert monitor.rescan() is True
    assert created[0].rescans == 1
    assert monitor.last_error == ""

    created[0].on_disconnect("stick-1")
    assert disconnected == ["stick-1"]
    assert monitor.devices == ()
    assert monitor.values("stick-1") == {}

    monitor.stop()
    monitor.stop()
    assert created[0].stops == 1
    assert monitor.available is False


@pytest.mark.parametrize(
    ("value", "expected"),
    [(-32768, -1.0), (-16384, -0.5), (0, 0.0), (32767, 1.0)],
)
def test_axis_normalization(value, expected):
    event = ControllerEvent("device", "axis", 0, value)
    assert event.normalized_axis == pytest.approx(expected)


def test_button_properties_do_not_treat_other_controls_as_pressed():
    assert ControllerEvent("device", "button", 0, 1).pressed is True
    assert ControllerEvent("device", "button", 0, 0).pressed is False
    axis = ControllerEvent("device", "axis", 0, 32767)
    assert axis.pressed is False
    assert ControllerEvent("device", "hat", 0, 1).normalized_axis is None


@pytest.mark.skipif(not _IS_LINUX, reason="Linux joystick API test")
def test_linux_device_parses_batched_and_partial_native_records(qapp):
    read_fd, write_fd = os.pipe()
    os.set_blocking(read_fd, False)
    events = []
    disconnected = []
    info = ControllerDevice("linux:test", "Test stick", "linux-js")
    device = LinuxJoystickDevice(
        Path("/dev/input/js9"),
        info,
        events.append,
        disconnected.append,
        qapp,
        fd=read_fd,
    )
    try:
        axis_initial = struct.pack("=IhBB", 10, -1234, 0x82, 3)
        button = struct.pack("=IhBB", 11, 1, 0x01, 7)
        unknown = struct.pack("=IhBB", 12, 99, 0x03, 1)
        payload = axis_initial + button + unknown

        os.write(write_fd, payload[:5])
        device._on_ready()
        assert events == []

        os.write(write_fd, payload[5:])
        device._on_ready()
        assert events == [
            ControllerEvent("linux:test", "axis", 3, -1234, 10, True),
            ControllerEvent("linux:test", "button", 7, 1, 11, False),
        ]
        assert disconnected == []
    finally:
        device.stop()
        os.close(write_fd)



@pytest.mark.skipif(not _IS_LINUX, reason="Linux joystick API test")
def test_linux_device_reports_eof_as_one_disconnect(qapp):
    read_fd, write_fd = os.pipe()
    os.set_blocking(read_fd, False)
    disconnected = []
    info = ControllerDevice("linux:test", "Test stick", "linux-js")
    device = LinuxJoystickDevice(
        Path("/dev/input/js9"),
        info,
        lambda _event: None,
        disconnected.append,
        qapp,
        fd=read_fd,
    )

    os.close(write_fd)
    device._on_ready()
    device._on_ready()
    device.stop(notify=True)

    assert disconnected == ["linux:test"]


@pytest.mark.skipif(not _IS_LINUX, reason="Linux joystick API test")
def test_axis_map_reports_the_abs_code_behind_each_dense_index():
    # A stick declaring only X, Y and RZ packs them densely, so RZ arrives as index 2, not 5 -- why an index alone can't name an axis.
    def fake_ioctl(_fd, request, data, _mutate):
        assert request == controller_linux._JSIOCGAXMAP
        data[:3] = bytes((0, 1, 5))
        return 0

    with mock.patch("fcntl.ioctl", fake_ioctl):
        assert controller_linux._ioctl_axis_map(3, 3) == (0, 1, 5)


@pytest.mark.skipif(not _IS_LINUX, reason="Linux joystick API test")
def test_axis_map_truncates_to_the_declared_axis_count():
    def fake_ioctl(_fd, _request, data, _mutate):
        data[:6] = bytes((0, 1, 2, 3, 4, 5))
        return 0

    with mock.patch("fcntl.ioctl", fake_ioctl):
        assert controller_linux._ioctl_axis_map(3, 2) == (0, 1)
        capped = controller_linux._ioctl_axis_map(3, 500)
        assert len(capped) == controller_linux._ABS_CNT


@pytest.mark.skipif(not _IS_LINUX, reason="Linux joystick API test")
def test_axis_map_skips_the_ioctl_when_there_are_no_axes():
    def unreachable(*_args, **_kwargs):
        raise AssertionError("no axes means nothing to map")

    with mock.patch("fcntl.ioctl", unreachable):
        assert controller_linux._ioctl_axis_map(3, 0) == ()


@pytest.mark.skipif(not _IS_LINUX, reason="Linux joystick API test")
def test_axis_map_unavailable_leaves_codes_empty():
    with mock.patch("fcntl.ioctl", side_effect=OSError(errno.ENOTTY, "nope")):
        assert controller_linux._ioctl_axis_map(3, 4) == ()


def test_device_without_axis_codes_reports_none():
    # SDL2 has no axis-map equivalent, so codes stay empty there.
    assert ControllerDevice("x", "Stick", "sdl2", axes=4).axis_codes == ()


class _DummyLinuxDevice:
    def __init__(self, path, on_disconnect):
        self.path = path
        self.info = ControllerDevice(
            f"linux:{path.name}", path.name, "linux-js", path=str(path)
        )
        self._on_disconnect = on_disconnect
        self._closed = False

    def stop(self, *, notify=False):
        if self._closed:
            return
        self._closed = True
        if notify:
            self._on_disconnect(self.info.id)


@pytest.mark.skipif(not _IS_LINUX, reason="Linux joystick API test")
def test_linux_backend_discovers_and_retires_device_nodes(qapp, tmp_path):
    js1 = tmp_path / "js1"
    js0 = tmp_path / "js0"
    js1.touch()
    js0.touch()
    connected = []
    disconnected = []
    opened = []

    def fake_open(path, _on_event, on_disconnect, _parent):
        opened.append(path.name)
        return _DummyLinuxDevice(path, on_disconnect)

    backend = LinuxControllerBackend(
        connected.append,
        disconnected.append,
        lambda _event: None,
        lambda _error: None,
        qapp,
        device_dir=tmp_path,
    )
    with mock.patch.object(LinuxJoystickDevice, "open", side_effect=fake_open):
        try:
            assert backend.start() is True
            assert opened == ["js0", "js1"]
            assert [device.id for device in connected] == [
                "linux:js0",
                "linux:js1",
            ]

            js0.unlink()
            backend.scan()
            assert disconnected == ["linux:js0"]
        finally:
            backend.stop()



@pytest.mark.skipif(not _IS_LINUX, reason="Linux joystick API test")
def test_linux_backend_deduplicates_open_errors_until_node_changes(qapp, tmp_path):
    path = tmp_path / "js0"
    path.touch()
    errors = []
    backend = LinuxControllerBackend(
        lambda _device: None,
        lambda _device_id: None,
        lambda _event: None,
        errors.append,
        qapp,
        device_dir=tmp_path,
    )
    denied = PermissionError(errno.EACCES, "Permission denied")
    with mock.patch.object(LinuxJoystickDevice, "open", side_effect=denied):
        try:
            backend.start()
            backend.scan()
            assert len(errors) == 1
            assert str(path) in errors[0]

            path.unlink()
            backend.scan()
            path.touch()
            backend.scan()
            assert len(errors) == 2
        finally:
            backend.stop()


class _FakeSdl:
    SDL_INIT_EVENTS = 0x01
    SDL_INIT_JOYSTICK = 0x02
    SDL_ENABLE = 1
    SDL_HINT_OVERRIDE = 2

    SDL_JOYAXISMOTION = 10
    SDL_JOYBALLMOTION = 11
    SDL_JOYHATMOTION = 12
    SDL_JOYBUTTONDOWN = 13
    SDL_JOYBUTTONUP = 14
    SDL_JOYDEVICEADDED = 15
    SDL_JOYDEVICEREMOVED = 16

    def __init__(self, devices=(), *, init_result=0, hint_result=1):
        self.devices = list(devices)
        self.init_result = init_result
        self.hint_result = hint_result
        self.hints = []
        self.init_flags = []
        self.quit_flags = []
        self.event_states = []
        self.updates = 0
        self.opened = []
        self.closed = []

    def _device(self, handle):
        return self.devices[handle.index]

    def SDL_SetHintWithPriority(self, name, value, priority):
        self.hints.append((name, value, priority))
        return self.hint_result

    def SDL_InitSubSystem(self, flags):
        self.init_flags.append(flags)
        return self.init_result

    def SDL_QuitSubSystem(self, flags):
        self.quit_flags.append(flags)

    def SDL_JoystickEventState(self, state):
        self.event_states.append(state)
        return state

    def SDL_JoystickUpdate(self):
        self.updates += 1

    def SDL_NumJoysticks(self):
        return len(self.devices)

    def SDL_JoystickNameForIndex(self, index):
        device = self.devices[index]
        return device.get("index_name", device["name"]).encode()

    def SDL_JoystickGetDeviceVendor(self, index):
        device = self.devices[index]
        return device.get("index_vendor", device.get("vendor", 0))

    def SDL_JoystickName(self, handle):
        return self._device(handle)["name"].encode()

    def SDL_JoystickGetVendor(self, handle):
        return self._device(handle).get("vendor", 0)

    def SDL_JoystickGetProduct(self, handle):
        return self._device(handle).get("product", 0)

    def SDL_JoystickGetGUID(self, handle):
        return self._device(handle).get("guid", f"guid-{handle.index}").encode()

    def SDL_JoystickOpen(self, index):
        self.opened.append(index)
        if self.devices[index].get("fail_open"):
            return None
        return SimpleNamespace(index=index)

    def SDL_JoystickClose(self, handle):
        self.closed.append(handle.index)

    def SDL_JoystickInstanceID(self, handle):
        return self._device(handle)["instance"]

    def SDL_JoystickGetSerial(self, handle):
        serial = self._device(handle).get("serial", "")
        return serial.encode() if serial else None

    def SDL_JoystickPath(self, handle):
        path = self._device(handle).get("path", "")
        return path.encode() if path else None

    def SDL_JoystickNumAxes(self, handle):
        return len(self._device(handle).get("axes", []))

    def SDL_JoystickNumButtons(self, handle):
        return len(self._device(handle).get("buttons", []))

    def SDL_JoystickNumHats(self, handle):
        return len(self._device(handle).get("hats", []))

    def SDL_JoystickNumBalls(self, handle):
        return self._device(handle).get("balls", 0)

    def SDL_JoystickGetAxis(self, handle, index):
        return self._device(handle)["axes"][index]

    def SDL_JoystickGetButton(self, handle, index):
        return self._device(handle)["buttons"][index]

    def SDL_JoystickGetHat(self, handle, index):
        return self._device(handle)["hats"][index]

    def SDL_GetError(self):
        return b"fake SDL error"



def _sdl_device(name="VKB Gladiator", instance=41, **overrides):
    values = {
        "name": name,
        "instance": instance,
        "vendor": VKB_VENDOR_ID,
        "product": 0x0200,
        "guid": f"GUID-{instance}",
        "serial": f"SERIAL-{instance}",
        "path": f"device-path-{instance}",
        "axes": [100, -200],
        "buttons": [0, 1],
        "hats": [0],
        "balls": 1,
    }
    values.update(overrides)
    return values



def test_vkb_identification_prefers_vid_with_legacy_name_fallback():
    assert is_vkb_device(VKB_VENDOR_ID, "anything") is True
    assert is_vkb_device(0, "VKBsim Gladiator") is True
    assert is_vkb_device(0x046D, "VKB-shaped Logitech") is False
    assert is_vkb_device(0, "Definitely not VKB") is False
    assert is_vkb_device(0, "Thrustmaster") is False



def test_sdl_backend_filters_devices_and_emits_initial_state(qapp):
    sdl = _FakeSdl(
        [
            _sdl_device("Logitech", 10, vendor=0x046D),
            _sdl_device(),
            _sdl_device("VKBsim legacy", 42, vendor=0, serial=""),
            _sdl_device("", 43, vendor=0),
        ]
    )
    connected = []
    events = []
    errors = []
    backend = Sdl2ControllerBackend(
        connected.append,
        lambda _device_id: None,
        events.append,
        errors.append,
        qapp,
        sdl=sdl,
    )
    try:
        assert backend.start() is True
        assert sdl.hints == [
            (b"SDL_JOYSTICK_ALLOW_BACKGROUND_EVENTS", b"1", sdl.SDL_HINT_OVERRIDE)
        ]
        assert sdl.opened == [1, 2]
        assert sdl.updates == 2
        assert [device.name for device in connected] == [
            "VKB Gladiator",
            "VKBsim legacy",
        ]
        assert connected[0].vendor_id == VKB_VENDOR_ID
        assert connected[0].product_id == 0x0200
        assert connected[0].axes == 2
        assert connected[0].buttons == 2
        assert connected[0].hats == 1
        assert connected[0].balls == 1
        assert [(event.kind, event.index, event.value, event.initial) for event in events[:5]] == [
            ("axis", 0, 100, True),
            ("axis", 1, -200, True),
            ("button", 0, 0, True),
            ("button", 1, 1, True),
            ("hat", 0, 0, True),
        ]
        assert errors == []
    finally:
        backend.stop()

    assert sorted(sdl.closed) == [1, 2]
    assert sdl.quit_flags == [sdl.SDL_INIT_EVENTS | sdl.SDL_INIT_JOYSTICK]


def test_sdl_open_failure_does_not_block_other_vkb_devices(qapp):
    sdl = _FakeSdl(
        [
            _sdl_device("Broken VKB", 40, fail_open=True),
            _sdl_device("Working VKB", 41, hats=[]),
        ]
    )
    connected = []
    errors = []
    backend = Sdl2ControllerBackend(
        connected.append,
        lambda _device_id: None,
        lambda _event: None,
        errors.append,
        qapp,
        sdl=sdl,
    )
    try:
        assert backend.start() is True
        assert [device.name for device in connected] == ["Working VKB"]
        assert errors == ["Cannot open VKB controller Broken VKB: fake SDL error"]
    finally:
        backend.stop()


def test_sdl_rechecks_vkb_identity_after_open(qapp):
    shifted = _sdl_device(
        "",
        41,
        vendor=0,
        index_name="VKB before hotplug",
        index_vendor=VKB_VENDOR_ID,
    )
    sdl = _FakeSdl([shifted])
    connected = []
    backend = Sdl2ControllerBackend(
        connected.append,
        lambda _device_id: None,
        lambda _event: None,
        pytest.fail,
        qapp,
        sdl=sdl,
    )
    try:
        assert backend.start() is True
        assert connected == []
        assert sdl.opened == [0]
        assert sdl.closed == [0]
    finally:
        backend.stop()


def test_sdl_device_id_is_stable_across_instance_ids(qapp):
    observed_ids = []
    for instance_id in (41, 99):
        sdl = _FakeSdl(
            [
                _sdl_device(
                    instance=instance_id,
                    serial="",
                    path="",
                    guid="same-guid",
                )
            ]
        )
        backend = Sdl2ControllerBackend(
            lambda device: observed_ids.append(device.id),
            lambda _device_id: None,
            lambda _event: None,
            pytest.fail,
            qapp,
            sdl=sdl,
        )
        try:
            assert backend.start() is True
        finally:
            backend.stop()

    assert observed_ids == ["sdl2:same-guid:device"] * 2


def test_sdl_rescan_retries_enumeration_without_duplicate_devices(qapp):
    sdl = _FakeSdl([_sdl_device()])
    connected = []
    backend = Sdl2ControllerBackend(
        connected.append,
        lambda _device_id: None,
        lambda _event: None,
        pytest.fail,
        qapp,
        sdl=sdl,
    )
    try:
        assert backend.start() is True
        with mock.patch.object(backend, "_poll") as poll:
            backend.rescan()

        assert len(connected) == 1
        assert sdl.opened == [0, 0]
        assert sdl.closed == [0]
        poll.assert_called_once_with()
    finally:
        backend.stop()



def test_sdl_backend_dispatches_raw_events_and_hotplug_ids(qapp):
    sdl = _FakeSdl([_sdl_device()])
    connected = []
    disconnected = []
    events = []
    backend = Sdl2ControllerBackend(
        connected.append,
        disconnected.append,
        events.append,
        pytest.fail,
        qapp,
        sdl=sdl,
    )
    try:
        backend.start()
        device_id = connected[0].id
        events.clear()

        backend._dispatch(
            SimpleNamespace(
                type=sdl.SDL_JOYAXISMOTION,
                jaxis=SimpleNamespace(which=41, axis=2, value=-32768, timestamp=100),
            )
        )
        backend._dispatch(
            SimpleNamespace(
                type=sdl.SDL_JOYBUTTONDOWN,
                jbutton=SimpleNamespace(which=41, button=7, state=1, timestamp=101),
            )
        )
        backend._dispatch(
            SimpleNamespace(
                type=sdl.SDL_JOYHATMOTION,
                jhat=SimpleNamespace(which=41, hat=1, value=3, timestamp=102),
            )
        )
        backend._dispatch(
            SimpleNamespace(
                type=sdl.SDL_JOYBALLMOTION,
                jball=SimpleNamespace(
                    which=41, ball=0, xrel=5, yrel=-2, timestamp=103
                ),
            )
        )
        backend._dispatch(
            SimpleNamespace(
                type=sdl.SDL_JOYAXISMOTION,
                jaxis=SimpleNamespace(which=999, axis=0, value=1, timestamp=104),
            )
        )

        assert events == [
            ControllerEvent(device_id, "axis", 2, -32768, 100),
            ControllerEvent(device_id, "button", 7, 1, 101),
            ControllerEvent(device_id, "hat", 1, 3, 102),
            ControllerEvent(device_id, "ball_x", 0, 5, 103),
            ControllerEvent(device_id, "ball_y", 0, -2, 103),
        ]

        sdl.devices.append(_sdl_device("VKB STECS", 77))
        backend._dispatch(
            SimpleNamespace(
                type=sdl.SDL_JOYDEVICEADDED,
                jdevice=SimpleNamespace(which=1),
            )
        )
        assert connected[-1].name == "VKB STECS"

        backend._dispatch(
            SimpleNamespace(
                type=sdl.SDL_JOYDEVICEREMOVED,
                jdevice=SimpleNamespace(which=41),
            )
        )
        assert disconnected == [device_id]
        assert 0 in sdl.closed
    finally:
        backend.stop()



def test_sdl_backend_reports_initialization_failure(qapp):
    sdl = _FakeSdl(init_result=-1)
    errors = []
    backend = Sdl2ControllerBackend(
        lambda _device: None,
        lambda _device_id: None,
        lambda _event: None,
        errors.append,
        qapp,
        sdl=sdl,
    )

    assert backend.start() is False
    assert backend.available is False
    assert errors == ["SDL2 controller initialization failed: fake SDL error"]
    assert sdl.quit_flags == []


def test_sdl_backend_requires_background_events(qapp):
    sdl = _FakeSdl(hint_result=0)
    errors = []
    backend = Sdl2ControllerBackend(
        lambda _device: None,
        lambda _device_id: None,
        lambda _event: None,
        errors.append,
        qapp,
        sdl=sdl,
    )

    assert backend.start() is False
    assert backend.available is False
    assert errors == ["SDL2 refused to enable background controller events"]
    assert sdl.init_flags == []
