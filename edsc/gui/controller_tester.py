"""Controller binding editor and optional live diagnostics."""

# SPDX-License-Identifier: GPL-3.0-or-later

from __future__ import annotations

import os
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QComboBox,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from ..controller_bindings import (
    CONTROLLER_ACTIONS,
    ControllerBinding,
    assign_binding,
    parse_bindings,
    serialize_bindings,
)
from ..platform.controller import (
    ControllerDevice,
    ControllerEvent,
    ControllerKind,
    ControllerMonitor,
    hat_direction,
)
from . import theme
from .controller_indicators import AxisIndicator, ButtonIndicator, HatIndicator

_BUTTON_COLUMNS = 8


def development_mode_enabled() -> bool:
    return os.environ.get("EDSC_DEV", "").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }


class ControllerTesterWidget(QWidget):
    """Hotplug-aware device picker, binding editor, and optional diagnostics."""

    def __init__(
        self,
        monitor: ControllerMonitor | None,
        parent: QWidget | None = None,
        *,
        selected_device_id: str = "",
        bindings: object = None,
        development_mode: bool = False,
    ) -> None:
        super().__init__(parent)
        self.monitor = monitor
        self.development_mode = bool(development_mode)
        self.bindings = parse_bindings(bindings)
        self._capturing_action: str | None = None
        self.binding_value_labels: dict[str, QLabel] = {}
        self.bind_buttons: dict[str, QPushButton] = {}
        self.clear_buttons: dict[str, QPushButton] = {}
        self.axis_indicators: dict[int, AxisIndicator] = {}
        self.button_indicators: dict[int, ButtonIndicator] = {}
        self.hat_indicators: dict[int, HatIndicator] = {}
        self.ball_indicators: dict[int, QLabel] = {}
        self._ball_values: dict[tuple[int, str], int] = {}
        self._observed_counts: dict[str, int] = {
            "axis": 0,
            "button": 0,
            "hat": 0,
            "ball": 0,
        }

        root = QVBoxLayout(self)
        root.setContentsMargins(*theme.METRICS.page_margins)
        root.setSpacing(theme.METRICS.content_spacing)

        title = QLabel("Controller bindings")
        theme.set_role(title, theme.SUBTITLE_ROLE)
        root.addWidget(title)

        note = QLabel(
            "Select the controller EDSC should listen to. Bind accepts the "
            "next button press or hat direction and works while the game has focus."
        )
        note.setWordWrap(True)
        theme.set_role(note, theme.MUTED_ROLE)
        root.addWidget(note)

        selector = QHBoxLayout()
        device_label = QLabel("Device:")
        selector.addWidget(device_label)
        self.device_combo = QComboBox()
        self.device_combo.setEditable(False)
        self.device_combo.setAccessibleName("Controller device")
        self.device_combo.setSizeAdjustPolicy(
            QComboBox.AdjustToMinimumContentsLengthWithIcon
        )
        self.device_combo.setMinimumContentsLength(28)
        self.device_combo.currentIndexChanged.connect(self._selection_changed)
        device_label.setBuddy(self.device_combo)
        selector.addWidget(self.device_combo, 1)
        self.rescan_button = QPushButton("Rescan")
        self.rescan_button.setToolTip("Ask the controller backend to scan again")
        self.rescan_button.setAccessibleDescription(
            "Retry controller detection immediately"
        )
        self.rescan_button.clicked.connect(self.rescan)
        selector.addWidget(self.rescan_button)
        root.addLayout(selector)

        self.status_label = QLabel()
        self.status_label.setWordWrap(True)
        theme.set_role(self.status_label, theme.MUTED_ROLE)
        root.addWidget(self.status_label)

        bindings_grid = QGridLayout()
        bindings_grid.setContentsMargins(*theme.METRICS.page_margins)
        bindings_grid.setSpacing(theme.METRICS.content_spacing)
        for row, (action_id, label) in enumerate(CONTROLLER_ACTIONS):
            action_label = QLabel(label)
            value_label = QLabel()
            value_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
            theme.set_role(value_label, theme.MUTED_ROLE)
            bind_button = QPushButton("Bind")
            bind_button.setAccessibleName(f"Bind {label}")
            bind_button.clicked.connect(
                lambda _checked=False, item=action_id: self._toggle_capture(item)
            )
            clear_button = QPushButton("Clear")
            clear_button.setAccessibleName(f"Clear {label}")
            clear_button.clicked.connect(
                lambda _checked=False, item=action_id: self.clear_binding(item)
            )
            self.binding_value_labels[action_id] = value_label
            self.bind_buttons[action_id] = bind_button
            self.clear_buttons[action_id] = clear_button
            bindings_grid.addWidget(action_label, row, 0)
            bindings_grid.addWidget(value_label, row, 1)
            bindings_grid.addWidget(bind_button, row, 2)
            bindings_grid.addWidget(clear_button, row, 3)
        bindings_grid.setColumnStretch(1, 1)
        root.addLayout(bindings_grid)

        self.capture_label = QLabel()
        self.capture_label.setWordWrap(True)
        theme.set_role(self.capture_label, theme.STATUS_ROLE)
        self.capture_label.hide()
        root.addWidget(self.capture_label)

        # The selector and binding editor above are normal user controls; raw metadata and the visual event stream below are development tooling.
        self.diagnostics = QWidget()
        diagnostics = QVBoxLayout(self.diagnostics)
        diagnostics.setContentsMargins(*theme.METRICS.page_margins)
        diagnostics.setSpacing(theme.METRICS.content_spacing)
        diagnostics_title = QLabel("Developer input tester")
        theme.set_role(diagnostics_title, theme.SUBTITLE_ROLE)
        diagnostics.addWidget(diagnostics_title)
        diagnostics_note = QLabel(
            "Move axes, hats, and buttons to inspect the global raw event stream."
        )
        diagnostics_note.setWordWrap(True)
        theme.set_role(diagnostics_note, theme.MUTED_ROLE)
        diagnostics.addWidget(diagnostics_note)

        self.device_details = QLabel()
        self.device_details.setWordWrap(True)
        self.device_details.setTextInteractionFlags(Qt.TextSelectableByMouse)
        diagnostics.addWidget(self.device_details)

        self.controls_scroll = QScrollArea()
        self.controls_scroll.setWidgetResizable(True)
        self.controls_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.controls_scroll.setMinimumHeight(270)
        diagnostics.addWidget(self.controls_scroll, 1)

        self.last_event_label = QLabel("Waiting for input…")
        self.last_event_label.setWordWrap(True)
        theme.set_role(self.last_event_label, theme.MUTED_ROLE)
        diagnostics.addWidget(self.last_event_label)
        self.diagnostics.setVisible(self.development_mode)
        root.addWidget(self.diagnostics, 1)

        if self.monitor is not None:
            self.monitor.device_connected.connect(self._device_connected)
            self.monitor.device_disconnected.connect(self._device_disconnected)
            self.monitor.event_received.connect(self._event_received)
            self.monitor.error.connect(self._controller_error)

        self._sync_devices(selected_device_id or None)
        self._update_binding_controls()

    @property
    def selected_device_id(self) -> str | None:
        value = self.device_combo.currentData()
        return value if isinstance(value, str) and value else None

    @property
    def binding_config(self) -> dict[str, dict[str, int | str]]:
        """JSON-safe snapshot suitable for ``Config.controller_bindings``."""
        return serialize_bindings(self.bindings)

    def rescan(self) -> None:
        if self.monitor is not None:
            self.monitor.rescan()
        self._sync_devices()

    def _sync_devices(self, preferred_id: str | None = None) -> None:
        previous_id = preferred_id or self.selected_device_id
        devices = self.monitor.devices if self.monitor is not None else ()

        self.device_combo.blockSignals(True)
        self.device_combo.clear()
        if not devices:
            if previous_id:
                self.device_combo.addItem(
                    "Configured controller (not connected)", previous_id
                )
            else:
                self.device_combo.addItem("No controllers detected", None)
            self.device_combo.setEnabled(False)
        else:
            device_labels = [
                (device, self._device_label(device)) for device in devices
            ]
            totals: dict[str, int] = {}
            for _, label in device_labels:
                totals[label] = totals.get(label, 0) + 1
            occurrences: dict[str, int] = {}
            for device, label in device_labels:
                occurrences[label] = occurrences.get(label, 0) + 1
                display = (
                    f"{label} ({occurrences[label]})"
                    if totals[label] > 1
                    else label
                )
                self.device_combo.addItem(display, device.id)
            if previous_id and self.device_combo.findData(previous_id) < 0:
                self.device_combo.insertItem(
                    0, "Configured controller (not connected)", previous_id
                )
            self.device_combo.setEnabled(True)
            selected = self.device_combo.findData(previous_id)
            self.device_combo.setCurrentIndex(selected if selected >= 0 else 0)
        self.device_combo.blockSignals(False)
        self.rescan_button.setEnabled(self.monitor is not None)
        self._selection_changed()

    @staticmethod
    def _device_label(device: ControllerDevice) -> str:
        name = device.name.strip() or "Unnamed controller"
        if device.path:
            connection = Path(device.path).name
            if connection and connection != name:
                return f"{name} — {connection}"
        return name

    def _selected_device(self) -> ControllerDevice | None:
        device_id = self.selected_device_id
        if self.monitor is None or device_id is None:
            return None
        return next(
            (device for device in self.monitor.devices if device.id == device_id),
            None,
        )

    def _selection_changed(self, *_args) -> None:
        self._stop_capture()
        device = self._selected_device()
        self._ball_values.clear()
        self._observed_counts = {"axis": 0, "button": 0, "hat": 0, "ball": 0}
        self.last_event_label.setText("Waiting for input…")

        if device is None:
            self.device_details.clear()
            self.device_details.setToolTip("")
            if self.development_mode:
                self._rebuild_controls(None, {})
            self._update_status()
            self._update_binding_controls()
            return

        values = self.monitor.values(device.id) if self.monitor is not None else {}
        for (kind, index) in values:
            key = "ball" if kind in ("ball_x", "ball_y") else kind
            self._observed_counts[key] = max(self._observed_counts[key], index + 1)

        identity = ""
        if device.vendor_id or device.product_id:
            identity = f" · USB {device.vendor_id:04X}:{device.product_id:04X}"
        self.device_details.setText(
            f"Backend: {device.backend}{identity}\n"
            f"Axes: {device.axes} · Buttons: {device.buttons} · "
            f"Hats: {device.hats} · Trackballs: {device.balls}"
        )
        self.device_details.setToolTip(device.path or device.id)
        if self.development_mode:
            self._rebuild_controls(device, values)
        self._update_status()
        self._update_binding_controls()

    def _toggle_capture(self, action_id: str) -> None:
        if self._capturing_action == action_id:
            self._stop_capture()
            return
        if self._selected_device() is None:
            return
        self._capturing_action = action_id
        label = dict(CONTROLLER_ACTIONS)[action_id]
        self.capture_label.setText(
            f"Listening for {label}… press a button or move a hat."
        )
        self.capture_label.show()
        self._update_binding_controls()

    def _stop_capture(self) -> None:
        if self._capturing_action is None and self.capture_label.isHidden():
            return
        self._capturing_action = None
        self.capture_label.clear()
        self.capture_label.hide()
        self._update_binding_controls()

    def clear_binding(self, action_id: str) -> None:
        self.bindings.pop(action_id, None)
        if self._capturing_action == action_id:
            self._stop_capture()
        else:
            self._update_binding_controls()

    def _update_binding_controls(self) -> None:
        has_device = self._selected_device() is not None
        for action_id, _label in CONTROLLER_ACTIONS:
            binding = self.bindings.get(action_id)
            self.binding_value_labels[action_id].setText(
                binding.describe() if binding is not None else "Not bound"
            )
            is_current = self._capturing_action == action_id
            self.bind_buttons[action_id].setText("Cancel" if is_current else "Bind")
            self.bind_buttons[action_id].setEnabled(
                has_device
                and (self._capturing_action is None or is_current)
            )
            self.clear_buttons[action_id].setEnabled(
                binding is not None and self._capturing_action is None
            )

    def _update_status(self) -> None:
        if self.monitor is None:
            self._set_status("Controller monitoring is unavailable in this window.")
            return
        count = len(self.monitor.devices)
        if count:
            noun = "device" if count == 1 else "devices"
            self._set_status(
                f"{count} {noun} detected · {self.monitor.backend_name}"
            )
        elif self.monitor.last_error:
            self._set_status(self.monitor.last_error, error=True)
        elif self.monitor.available:
            self._set_status(
                f"No controllers detected · listening via {self.monitor.backend_name}"
            )
        else:
            self._set_status(
                "Controller capture is unavailable. Select Rescan to retry.",
                error=True,
            )

    def _set_status(self, message: str, *, error: bool = False) -> None:
        role = theme.ERROR_ROLE if error else theme.MUTED_ROLE
        if self.status_label.objectName() != role:
            theme.set_role(self.status_label, role)
            self.status_label.style().unpolish(self.status_label)
            self.status_label.style().polish(self.status_label)
        self.status_label.setText(message)

    def _rebuild_controls(
        self,
        device: ControllerDevice | None,
        values: dict[tuple[ControllerKind, int], int],
    ) -> None:
        old_host = self.controls_scroll.takeWidget()
        if old_host is not None:
            old_host.deleteLater()

        self.axis_indicators = {}
        self.button_indicators = {}
        self.hat_indicators = {}
        self.ball_indicators = {}

        host = QWidget()
        root = QVBoxLayout(host)
        root.setContentsMargins(*theme.METRICS.panel_margins)
        root.setSpacing(theme.METRICS.content_spacing)

        if device is None:
            empty = QLabel("Connect a controller to begin testing.")
            empty.setAlignment(Qt.AlignCenter)
            theme.set_role(empty, theme.MUTED_ROLE)
            root.addWidget(empty, 1)
            self.controls_scroll.setWidget(host)
            return

        axis_count = max(device.axes, self._observed_counts["axis"])
        button_count = max(device.buttons, self._observed_counts["button"])
        hat_count = max(device.hats, self._observed_counts["hat"])
        ball_count = max(device.balls, self._observed_counts["ball"])

        if axis_count:
            root.addWidget(self._section_label("Axes"))
            for index in range(axis_count):
                indicator = AxisIndicator(index)
                indicator.set_value(values.get(("axis", index), 0))
                self.axis_indicators[index] = indicator
                root.addWidget(indicator)

        if button_count:
            root.addWidget(self._section_label("Buttons"))
            grid = QGridLayout()
            grid.setContentsMargins(*theme.METRICS.page_margins)
            grid.setSpacing(theme.METRICS.content_spacing)
            for index in range(button_count):
                indicator = ButtonIndicator(index)
                indicator.set_pressed(bool(values.get(("button", index), 0)))
                self.button_indicators[index] = indicator
                grid.addWidget(
                    indicator,
                    index // _BUTTON_COLUMNS,
                    index % _BUTTON_COLUMNS,
                )
            grid.setColumnStretch(_BUTTON_COLUMNS, 1)
            root.addLayout(grid)

        if hat_count:
            root.addWidget(self._section_label("Hats"))
            hats = QGridLayout()
            hats.setContentsMargins(*theme.METRICS.page_margins)
            hats.setSpacing(theme.METRICS.content_spacing)
            for index in range(hat_count):
                indicator = HatIndicator(index)
                indicator.set_value(values.get(("hat", index), 0))
                self.hat_indicators[index] = indicator
                hats.addWidget(indicator, index // 3, index % 3)
            root.addLayout(hats)

        if ball_count:
            root.addWidget(self._section_label("Trackballs"))
            for index in range(ball_count):
                label = QLabel(self._ball_text(index))
                self.ball_indicators[index] = label
                root.addWidget(label)

        if not any((axis_count, button_count, hat_count, ball_count)):
            empty = QLabel(
                "This device reported no controls yet. Move an axis or press a button."
            )
            empty.setWordWrap(True)
            theme.set_role(empty, theme.MUTED_ROLE)
            root.addWidget(empty)

        root.addStretch(1)
        self.controls_scroll.setWidget(host)

    @staticmethod
    def _section_label(text: str) -> QLabel:
        label = QLabel(text)
        theme.set_role(label, theme.SUBTITLE_ROLE)
        return label

    def _device_connected(self, device: ControllerDevice) -> None:
        preferred = self.selected_device_id or device.id
        self._sync_devices(preferred)

    def _device_disconnected(self, device_id: str) -> None:
        self._sync_devices(self.selected_device_id)

    def _event_received(self, event: ControllerEvent) -> None:
        if event.device_id != self.selected_device_id:
            return

        if self._capturing_action is not None:
            binding = ControllerBinding.from_event(event)
            if binding is not None:
                assign_binding(self.bindings, self._capturing_action, binding)
                self._stop_capture()

        if not self.development_mode:
            return

        count_key = "ball" if event.kind in ("ball_x", "ball_y") else event.kind
        if event.index >= self._observed_counts[count_key]:
            self._observed_counts[count_key] = event.index + 1
            device = self._selected_device()
            values = (
                self.monitor.values(event.device_id)
                if self.monitor is not None
                else {}
            )
            self._rebuild_controls(device, values)

        if event.kind == "axis":
            indicator = self.axis_indicators.get(event.index)
            if indicator is not None:
                indicator.set_value(event.value)
            normalized = event.normalized_axis
            detail = f"{event.value:+d} ({normalized:+.3f})"
        elif event.kind == "button":
            indicator = self.button_indicators.get(event.index)
            if indicator is not None:
                indicator.set_pressed(event.pressed)
            detail = "Pressed" if event.pressed else "Released"
        elif event.kind == "hat":
            indicator = self.hat_indicators.get(event.index)
            if indicator is not None:
                indicator.set_value(event.value)
            detail = f"{hat_direction(event.value)} (0x{event.value:02X})"
        else:
            axis = "x" if event.kind == "ball_x" else "y"
            self._ball_values[(event.index, axis)] = event.value
            indicator = self.ball_indicators.get(event.index)
            if indicator is not None:
                indicator.setText(self._ball_text(event.index))
            detail = f"Δ{axis.upper()} {event.value:+d}"

        source = "Initial" if event.initial else "Live"
        name = event.kind.replace("_", " ").title()
        self.last_event_label.setText(
            f"{source}: {name} {event.index} · {detail}"
        )

    def _ball_text(self, index: int) -> str:
        x_value = self._ball_values.get((index, "x"), 0)
        y_value = self._ball_values.get((index, "y"), 0)
        return f"Ball {index}: ΔX {x_value:+d} · ΔY {y_value:+d}"

    def _controller_error(self, message: str) -> None:
        if not self.monitor or not self.monitor.devices:
            self._set_status(message, error=True)
