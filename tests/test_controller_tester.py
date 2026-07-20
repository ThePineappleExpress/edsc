
import pytest
from PySide6.QtCore import QObject, Signal

from edsc.config import Config
from edsc.gui import theme
from edsc.gui.controller_tester import (
    AxisIndicator,
    ButtonIndicator,
    ControllerTesterWidget,
    HatIndicator,
    development_mode_enabled,
    hat_direction,
)
from edsc.gui.settings_dialog import SettingsDialog
from edsc.platform.controller import ControllerDevice, ControllerEvent


class _FakeMonitor(QObject):
    device_connected = Signal(object)
    device_disconnected = Signal(str)
    event_received = Signal(object)
    error = Signal(str)

    def __init__(self):
        super().__init__()
        self.backend_name = "test-backend"
        self.available = True
        self.last_error = ""
        self._devices = {}
        self._values = {}
        self.rescans = 0
        self.on_rescan = None

    @property
    def devices(self):
        return tuple(self._devices[key] for key in sorted(self._devices))

    def values(self, device_id):
        return dict(self._values.get(device_id, {}))

    def add_device(self, device):
        self._devices[device.id] = device
        self._values[device.id] = {}
        self.device_connected.emit(device)

    def remove_device(self, device_id):
        self._devices.pop(device_id, None)
        self._values.pop(device_id, None)
        self.device_disconnected.emit(device_id)

    def send(self, event):
        if event.kind not in ("ball_x", "ball_y"):
            self._values[event.device_id][(event.kind, event.index)] = event.value
        self.event_received.emit(event)

    def fail(self, message):
        self.last_error = message
        self.error.emit(message)

    def rescan(self):
        self.rescans += 1
        if self.on_rescan is not None:
            self.on_rescan()
        return self.available


@pytest.fixture
def monitor():
    return _FakeMonitor()


@pytest.fixture
def tester(qapp, monitor):
    widget = ControllerTesterWidget(monitor, development_mode=True)
    yield widget
    widget.deleteLater()
    qapp.processEvents()


def _device(device_id="stick-1", name="VKB Gladiator", **overrides):
    values = {
        "id": device_id,
        "name": name,
        "backend": "test-backend",
        "vendor_id": 0x231D,
        "product_id": 0x0200,
        "serial": "ABC123",
        "path": "/dev/input/js0",
        "axes": 2,
        "buttons": 4,
        "hats": 1,
        "balls": 1,
    }
    values.update(overrides)
    return ControllerDevice(**values)


def test_empty_tester_shows_detection_state_and_rescans(tester, monitor):
    assert tester.device_combo.count() == 1
    assert tester.device_combo.currentText() == "No controllers detected"
    assert not tester.device_combo.isEnabled()
    assert "No controllers detected" in tester.status_label.text()
    assert tester.selected_device_id is None
    assert tester.device_combo.accessibleName() == "Controller device"

    tester.rescan_button.click()

    assert monitor.rescans == 1


def test_device_dropdown_and_capabilities_update_on_hotplug(tester, monitor):
    first = _device()
    second = _device(
        "stick-2",
        "VKB STECS",
        path="/dev/input/js1",
        axes=3,
        buttons=2,
        hats=0,
        balls=0,
    )

    monitor.add_device(first)
    monitor.add_device(second)

    assert tester.device_combo.count() == 2
    assert tester.device_combo.currentData() == first.id
    assert tester.device_combo.itemText(0) == "VKB Gladiator — js0"
    assert tester.device_combo.itemText(1) == "VKB STECS — js1"
    assert "2 devices detected" in tester.status_label.text()
    assert "USB 231D:0200" in tester.device_details.text()
    assert len(tester.axis_indicators) == 2
    assert len(tester.button_indicators) == 4
    assert len(tester.hat_indicators) == 1
    assert len(tester.ball_indicators) == 1

    tester.device_combo.setCurrentIndex(tester.device_combo.findData(second.id))

    assert tester.selected_device_id == second.id
    assert len(tester.axis_indicators) == 3
    assert len(tester.button_indicators) == 2
    assert tester.hat_indicators == {}
    assert tester.ball_indicators == {}


def test_duplicate_and_unnamed_devices_have_clear_dropdown_labels(tester, monitor):
    monitor.add_device(_device("one", "", path=""))
    monitor.add_device(_device("two", "", path=""))

    assert tester.device_combo.itemText(0) == "Unnamed controller (1)"
    assert tester.device_combo.itemText(1) == "Unnamed controller (2)"


def test_live_events_update_only_the_selected_device(tester, monitor):
    selected = _device()
    other = _device("stick-2", "VKB STECS", path="/dev/input/js1")
    monitor.add_device(selected)
    monitor.add_device(other)

    monitor.send(ControllerEvent(other.id, "axis", 0, 12345))
    assert tester.axis_indicators[0].value == 0

    monitor.send(ControllerEvent(selected.id, "axis", 0, -16384, 10))
    assert tester.axis_indicators[0].value == -16384
    assert tester.axis_indicators[0].normalized == pytest.approx(-0.5)
    assert "-16384 (-0.500)" in tester.last_event_label.text()

    monitor.send(ControllerEvent(selected.id, "button", 2, 1, 11))
    assert tester.button_indicators[2].pressed is True
    assert "Pressed" in tester.last_event_label.text()

    monitor.send(ControllerEvent(selected.id, "hat", 0, 0x03, 12))
    assert tester.hat_indicators[0].direction == "Up + Right"
    assert "Up + Right (0x03)" in tester.last_event_label.text()

    monitor.send(ControllerEvent(selected.id, "ball_x", 0, 7, 13))
    monitor.send(ControllerEvent(selected.id, "ball_y", 0, -4, 14))
    assert "ΔX +7 · ΔY -4" in tester.ball_indicators[0].text()


def test_snapshot_state_appears_when_switching_devices(tester, monitor):
    first = _device()
    second = _device("stick-2", "VKB STECS", path="/dev/input/js1")
    monitor.add_device(first)
    monitor.add_device(second)
    monitor.send(ControllerEvent(second.id, "axis", 1, 32767))
    monitor.send(ControllerEvent(second.id, "button", 3, 1))

    tester.device_combo.setCurrentIndex(tester.device_combo.findData(second.id))

    assert tester.axis_indicators[1].normalized == pytest.approx(1.0)
    assert tester.button_indicators[3].pressed is True


def test_undeclared_controls_are_added_when_events_arrive(tester, monitor):
    device = _device(axes=0, buttons=0, hats=0, balls=0)
    monitor.add_device(device)

    monitor.send(ControllerEvent(device.id, "button", 9, 1))

    assert len(tester.button_indicators) == 10
    assert tester.button_indicators[9].pressed is True


def test_selected_disconnect_is_preserved_until_the_device_returns(tester, monitor):
    first = _device()
    second = _device("stick-2", "VKB STECS", path="/dev/input/js1")
    monitor.add_device(first)
    monitor.add_device(second)
    tester.device_combo.setCurrentIndex(tester.device_combo.findData(second.id))

    monitor.remove_device(second.id)
    assert tester.selected_device_id == second.id
    assert tester.device_combo.currentText() == "Configured controller (not connected)"
    assert not tester.bind_buttons["next_tab"].isEnabled()

    monitor.remove_device(first.id)
    assert tester.selected_device_id == second.id

    monitor.add_device(second)
    assert tester.selected_device_id == second.id
    assert tester.device_combo.currentText() == "VKB STECS — js1"


def test_rescan_can_populate_the_dropdown(tester, monitor):
    device = _device()
    monitor.on_rescan = lambda: monitor.add_device(device)

    tester.rescan()

    assert tester.selected_device_id == device.id
    assert monitor.rescans == 1


def test_backend_error_is_visible_without_devices(tester, monitor):
    monitor.fail("Cannot open /dev/input/js0: Permission denied")

    assert "Permission denied" in tester.status_label.text()
    assert tester.status_label.objectName() == theme.ERROR_ROLE


def test_normal_mode_keeps_bindings_but_hides_raw_diagnostics(qapp, monitor):
    widget = ControllerTesterWidget(monitor, development_mode=False)

    assert widget.diagnostics.isHidden()
    assert widget.binding_value_labels["previous_tab"].text() == "Not bound"
    assert not widget.bind_buttons["previous_tab"].isEnabled()

    widget.deleteLater()
    qapp.processEvents()


@pytest.mark.parametrize(
    ("value", "enabled"),
    [("1", True), ("true", True), ("ON", True), ("0", False), ("", False)],
)
def test_development_mode_environment(monkeypatch, value, enabled):
    monkeypatch.setenv("EDSC_DEV", value)
    assert development_mode_enabled() is enabled


def test_binding_capture_accepts_only_selected_activation_edges(tester, monitor):
    selected = _device()
    other = _device("stick-2", "VKB STECS", path="/dev/input/js1")
    monitor.add_device(selected)
    monitor.add_device(other)

    tester.bind_buttons["next_tab"].click()
    assert tester.bind_buttons["next_tab"].text() == "Cancel"
    assert "Listening for Next tab" in tester.capture_label.text()

    monitor.send(ControllerEvent(selected.id, "axis", 0, 32767))
    monitor.send(ControllerEvent(selected.id, "button", 4, 1, initial=True))
    monitor.send(ControllerEvent(selected.id, "button", 4, 0))
    monitor.send(ControllerEvent(other.id, "button", 7, 1))
    assert tester.bind_buttons["next_tab"].text() == "Cancel"

    monitor.send(ControllerEvent(selected.id, "button", 4, 1))

    assert tester.binding_config["next_tab"] == {
        "kind": "button",
        "index": 4,
        "value": 1,
    }
    assert tester.binding_value_labels["next_tab"].text() == "Button 4"
    assert tester.capture_label.isHidden()


def test_hat_capture_reassigns_conflicting_control_and_can_clear(tester, monitor):
    device = _device()
    monitor.add_device(device)

    tester.bind_buttons["previous_tab"].click()
    monitor.send(ControllerEvent(device.id, "hat", 0, 0x03))
    assert tester.binding_value_labels["previous_tab"].text() == (
        "Hat 0 Up + Right"
    )

    tester.bind_buttons["refresh_search"].click()
    monitor.send(ControllerEvent(device.id, "hat", 0, 0))
    assert tester.bind_buttons["refresh_search"].text() == "Cancel"
    monitor.send(ControllerEvent(device.id, "hat", 0, 0x03))

    assert "previous_tab" not in tester.binding_config
    assert tester.binding_value_labels["previous_tab"].text() == "Not bound"
    assert tester.binding_value_labels["refresh_search"].text() == (
        "Hat 0 Up + Right"
    )

    tester.clear_buttons["refresh_search"].click()
    assert tester.binding_config == {}


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (0x00, "Centered"),
        (0x01, "Up"),
        (0x03, "Up + Right"),
        (0x06, "Right + Down"),
        (0x0C, "Down + Left"),
    ],
)
def test_hat_direction(value, expected):
    assert hat_direction(value) == expected


def test_indicators_render_offscreen(qapp):
    widgets = [AxisIndicator(0), ButtonIndicator(1), HatIndicator(2)]
    widgets[0].set_value(-32768)
    widgets[1].set_pressed(True)
    widgets[2].set_value(0x09)

    for widget in widgets:
        widget.resize(widget.sizeHint())
        pixmap = widget.grab()
        assert not pixmap.isNull()
        widget.deleteLater()
    qapp.processEvents()


def test_settings_controls_tab_owns_the_injected_tester(qapp, monitor):
    dialog = SettingsDialog(Config(), controllers=monitor)

    assert dialog.controller_tester.monitor is monitor
    assert dialog.controller_tester.diagnostics.isHidden()

    dialog.deleteLater()
    qapp.processEvents()
