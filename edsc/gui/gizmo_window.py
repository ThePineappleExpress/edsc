"""Frameless gizmo windows and the controller that feeds them; HUD decals with no chrome or background, above everything and transparent to the mouse while the game has focus -- alt-tab away and they become draggable, the same bargain the overlay strikes."""

# SPDX-License-Identifier: GPL-3.0-or-later

from __future__ import annotations

import json
import time
from collections.abc import Mapping
from dataclasses import replace
from pathlib import Path

from PySide6.QtCore import QObject, QPoint, QPointF, Qt, QTimer, Signal
from PySide6.QtGui import QMouseEvent, QPainter, QPen
from PySide6.QtWidgets import QApplication, QVBoxLayout, QWidget

from ..binds import load_binds, load_device_mappings
from ..boost import BoostTracker, read_loadout
from ..config import Config
from ..flight_axes import FlightMapping, FlightTracker, resolve_mapping
from ..flight_state import FlightStateTracker
from ..journal import locator
from ..platform import clickthrough, topmost
from ..platform.controller import ControllerEvent, ControllerMonitor
from . import theme
from .gizmo import BASE_SIZE, RotationGizmo, ThrustGizmo

# The gizmos redraw from accumulated state on a timer, not per event: a HOTAS reports at 125Hz+ and repainting a translucent always-on-top window over a GPU-bound game that often is pure waste.
REFRESH_MS = 33  # ~30Hz

# Where an unplaced pair lands: inset from the bottom-left, side by side.
_DEFAULT_MARGIN = 40
_DEFAULT_GAP = 20

# The manual aim crosshair: a reticle you drag to set where a gizmo's forward axis points; big enough to grab, small enough not to blot out the game behind.
AIM_TARGET_SIZE = 56
_AIM_RING_RADIUS = 7.0
_AIM_ARM_GAP = 5.0


def latest_loadout(journal_dir: Path) -> dict | None:
    """The most recent ``Loadout`` event in the newest journal, if any; the engine replays journals before the gizmos listen, so the current ship is read from the file rather than waited for live."""
    newest = locator.latest_journal(journal_dir)
    if newest is None:
        return None
    found: dict | None = None
    try:
        for line in newest.read_text(encoding="utf-8", errors="replace").splitlines():
            if '"Loadout"' not in line:
                continue
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(event, dict) and event.get("event") == "Loadout":
                found = event
    except OSError:
        return None
    return found


class GizmoWindow(QWidget):
    """One frameless, background-less, always-on-top gizmo."""

    def __init__(self, gizmo: QWidget, name: str) -> None:
        super().__init__()
        self._press_offset: QPoint | None = None
        self._dragged = False
        self._click_through = False
        self.gizmo = gizmo
        self.setWindowTitle(f"EDSC {name}")
        self.setAttribute(Qt.WA_TranslucentBackground)
        # Tool keeps it off the taskbar and out of the alt-tab list.
        self.setWindowFlags(
            Qt.FramelessWindowHint | Qt.Tool | Qt.WindowStaysOnTopHint
        )
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(gizmo)
        self.resize(gizmo.sizeHint())

    def apply_scale(self, scale: float) -> None:
        self.gizmo.set_scale(scale)
        self.resize(self.gizmo.sizeHint())

    def set_click_through(self, enabled: bool) -> None:
        """Pass the mouse through, or take it back so the window can be dragged."""
        if enabled == self._click_through:
            return
        clickthrough.set_click_through(self, enabled)
        self._click_through = enabled

    def assert_above(self) -> None:
        """Re-assert keep-above; the overlay shares the same stacking layer so whichever raised last wins, and the gizmos must reassert after it or they sink beneath it."""
        topmost.assert_above(self)

    #  drag to move

    def mousePressEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.LeftButton:
            self._press_offset = (
                event.globalPosition().toPoint() - self.frameGeometry().topLeft()
            )
            self._dragged = False

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        if self._press_offset is None or not (event.buttons() & Qt.LeftButton):
            return
        target = event.globalPosition().toPoint() - self._press_offset
        if (
            self._dragged
            or (target - self.pos()).manhattanLength()
            > QApplication.startDragDistance()
        ):
            self._dragged = True
            self.move(target)

    def mouseReleaseEvent(self, _event: QMouseEvent) -> None:
        self._press_offset = None

    def moveEvent(self, event) -> None:
        # The gizmo leans towards the middle of the screen, so moving the window changes what it should draw.
        super().moveEvent(event)
        self.gizmo.refresh_aim()

    @property
    def was_dragged(self) -> bool:
        return self._dragged


class AimTarget(QWidget):
    """A crosshair with a circle at its centre, drawn in the HUD colour; it marks a gizmo's vanishing point, living on the x/y plane at fixed depth (z = 1, into the screen), so it only ever moves sideways."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setFixedSize(AIM_TARGET_SIZE, AIM_TARGET_SIZE)

    def paintEvent(self, _event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.setPen(QPen(theme.ORANGE, 1.5))
        painter.setBrush(Qt.NoBrush)
        centre = QPointF(self.width() / 2.0, self.height() / 2.0)
        painter.drawEllipse(centre, _AIM_RING_RADIUS, _AIM_RING_RADIUS)
        # Four arms reaching the edges, with a gap so they clear the circle.
        gap = _AIM_RING_RADIUS + _AIM_ARM_GAP
        cx, cy = centre.x(), centre.y()
        painter.drawLine(QPointF(cx, 0.0), QPointF(cx, cy - gap))
        painter.drawLine(QPointF(cx, cy + gap), QPointF(cx, self.height()))
        painter.drawLine(QPointF(0.0, cy), QPointF(cx - gap, cy))
        painter.drawLine(QPointF(cx + gap, cy), QPointF(self.width(), cy))


class AimTargetWindow(QWidget):
    """A frameless, on-top, draggable window carrying one aim crosshair; a drag emits :attr:`dragged` so the gizmo re-aims and the point is saved, while programmatic placement stays silent so restoring a saved point never reads as a fresh edit."""

    dragged = Signal()

    def __init__(self, name: str) -> None:
        super().__init__()
        self._press_offset: QPoint | None = None
        self.setWindowTitle(f"EDSC {name} target")
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setWindowFlags(
            Qt.FramelessWindowHint | Qt.Tool | Qt.WindowStaysOnTopHint
        )
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self.crosshair = AimTarget()
        layout.addWidget(self.crosshair)
        self.resize(self.crosshair.sizeHint())

    def aim_point(self) -> QPoint:
        """Global position of the crosshair's centre -- what a gizmo aims at."""
        return self.mapToGlobal(self.rect().center())

    def move_to_aim(self, point: QPoint) -> None:
        """Place the window so its centre lands on ``point`` (global coords)."""
        offset = self.rect().center()
        self.move(point.x() - offset.x(), point.y() - offset.y())

    #  drag to move

    def mousePressEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.LeftButton:
            self._press_offset = (
                event.globalPosition().toPoint() - self.frameGeometry().topLeft()
            )

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        if self._press_offset is None or not (event.buttons() & Qt.LeftButton):
            return
        self.move(event.globalPosition().toPoint() - self._press_offset)
        self.dragged.emit()

    def mouseReleaseEvent(self, _event: QMouseEvent) -> None:
        self._press_offset = None


class GizmoController(QObject):
    """Owns both gizmo windows, their trackers, and the refresh timer."""

    def __init__(
        self,
        config: Config,
        monitor: ControllerMonitor | None,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self.config = config
        self.monitor = monitor
        self.mapping = FlightMapping.from_config(config.flight_mapping)
        self.tracker = FlightTracker(
            self.mapping, apply_deadzones=bool(config.gizmo_apply_deadzone)
        )
        self.flight_state = FlightStateTracker()
        self.boost = BoostTracker()
        self._journal_dir: Path | None = None
        self._last_boost_state = self.boost.state
        self._last_tick: float | None = None
        self._game_focused = False
        self._dirty = True
        # Whether the saved config actually placed these, or we defaulted them.
        self._placed = True

        self.thrust = GizmoWindow(
            ThrustGizmo(
                scale=config.gizmo_scale, font_pt=config.font_point_size
            ),
            "Thrust",
        )
        self.rotation = GizmoWindow(
            RotationGizmo(
                scale=config.gizmo_scale, font_pt=config.font_point_size
            ),
            "Rotation",
        )
        self._restore_positions()

        # Draggable aim crosshairs, shown only while the player edits them from settings; each is paired with its gizmo and the config keys that remember where it points.
        self.thrust_target = AimTargetWindow("Thrust")
        self.rotation_target = AimTargetWindow("Rotation")
        self._targets = (
            (
                self.thrust_target,
                self.thrust.gizmo,
                "gizmo_thrust_target_x",
                "gizmo_thrust_target_y",
            ),
            (
                self.rotation_target,
                self.rotation.gizmo,
                "gizmo_rotation_target_x",
                "gizmo_rotation_target_y",
            ),
        )
        # Whether the crosshairs are currently parented to an editing dialog.
        self._targeting = False
        for window, gizmo, key_x, key_y in self._targets:
            window.dragged.connect(
                lambda w=window, g=gizmo, kx=key_x, ky=key_y: self._on_target_dragged(
                    w, g, kx, ky
                )
            )
        self.restore_aim_targets()

        self._timer = QTimer(self)
        self._timer.setInterval(REFRESH_MS)
        self._timer.timeout.connect(self._tick)

        if monitor is not None:
            monitor.event_received.connect(self._on_controller_event)
            monitor.device_connected.connect(lambda _d: self.reresolve())
            monitor.device_disconnected.connect(lambda _d: self.reresolve())

    #  lifecycle

    @property
    def windows(self) -> tuple[GizmoWindow, GizmoWindow]:
        return (self.thrust, self.rotation)

    def _restore_positions(self) -> None:
        """Place both windows: saved coordinates, else a sensible default; unplaced windows would both sit at (0, 0) stacked in the corner, reading as one broken smudge."""
        saved = (
            (self.thrust, self.config.gizmo_thrust_x, self.config.gizmo_thrust_y),
            (
                self.rotation,
                self.config.gizmo_rotation_x,
                self.config.gizmo_rotation_y,
            ),
        )
        defaults = self._default_positions()
        for (window, x, y), fallback in zip(saved, defaults, strict=True):
            if isinstance(x, int) and isinstance(y, int):
                window.move(x, y)
            else:
                window.move(fallback)
                self._placed = False

    def _default_positions(self) -> tuple[QPoint, QPoint]:
        """Side by side along the bottom-left, clear of each other."""
        side = max(self.thrust.width(), self.rotation.width()) or BASE_SIZE
        screen = QApplication.primaryScreen()
        if screen is None:
            return QPoint(_DEFAULT_MARGIN, _DEFAULT_MARGIN), QPoint(
                _DEFAULT_MARGIN * 2 + side, _DEFAULT_MARGIN
            )
        area = screen.availableGeometry()
        top = area.bottom() - side - _DEFAULT_MARGIN
        left = area.left() + _DEFAULT_MARGIN
        return QPoint(left, top), QPoint(left + side + _DEFAULT_GAP, top)

    def save_positions(self) -> None:
        """Persist wherever the windows were dragged to; windows the user never placed keep their ``None``, so a default-placed pair is re-derived next launch instead of frozen where they landed."""
        if not self._placed and not any(w.was_dragged for w in self.windows):
            return
        self.config.gizmo_thrust_x = self.thrust.x()
        self.config.gizmo_thrust_y = self.thrust.y()
        self.config.gizmo_rotation_x = self.rotation.x()
        self.config.gizmo_rotation_y = self.rotation.y()

    #  aim targets

    @staticmethod
    def _config_point(x: object, y: object) -> QPoint | None:
        """A saved crosshair point, or ``None`` when it was never placed."""
        return QPoint(x, y) if isinstance(x, int) and isinstance(y, int) else None

    def restore_aim_targets(self) -> None:
        """Point each gizmo at its saved manual target, if any; called on construction and whenever the crosshairs hide, so a committed target survives and an un-dragged preview falls back to the auto aim."""
        for _window, gizmo, key_x, key_y in self._targets:
            gizmo.set_aim_target(
                self._config_point(
                    getattr(self.config, key_x), getattr(self.config, key_y)
                )
            )

    def _default_target(self, gizmo: QWidget, offset: int) -> QPoint:
        """A starting crosshair spot: the vanishing point, nudged aside; both gizmos aim at screen centre by default, so an un-nudged pair would stack -- offsetting keeps each grabbable."""
        screen = gizmo.screen() or QApplication.primaryScreen()
        centre = screen.geometry().center() if screen is not None else QPoint(0, 0)
        return QPoint(centre.x() + offset, centre.y())

    def show_targets(self, parent: QWidget | None = None) -> None:
        """Reveal the aim crosshairs for editing, above ``parent``; parenting to the modal settings dialog keeps them interactive, each starting on its saved point (or spread around the vanishing point when it has none), and placing is silent so only a drag commits/persists while the gizmo previews immediately."""
        self._targeting = parent is not None
        spread = self.thrust_target.width()
        for (window, gizmo, key_x, key_y), offset in zip(
            self._targets, (-spread, spread), strict=True
        ):
            saved = self._config_point(
                getattr(self.config, key_x), getattr(self.config, key_y)
            )
            point = saved if saved is not None else self._default_target(gizmo, offset)
            if parent is not None:
                window.setParent(parent, window.windowFlags())
            window.move_to_aim(point)
            gizmo.set_aim_target(point)
            window.show()
            window.raise_()

    def hide_targets(self) -> None:
        """Hide the crosshairs and restore each gizmo's committed aim; detaching from the settings dialog first keeps the windows alive past it, and an un-dragged preview reverts to whatever config holds."""
        for window, _gizmo, _key_x, _key_y in self._targets:
            if self._targeting:
                window.setParent(None, window.windowFlags())
            window.hide()
        self._targeting = False
        self.restore_aim_targets()

    def _on_target_dragged(
        self, window: AimTargetWindow, gizmo: QWidget, key_x: str, key_y: str
    ) -> None:
        """A crosshair moved: re-aim its gizmo and remember where it points."""
        point = window.aim_point()
        gizmo.set_aim_target(point)
        setattr(self.config, key_x, point.x())
        setattr(self.config, key_y, point.y())

    def import_from_binds(self, journal_dir=None) -> FlightMapping:
        """Read Elite's own bindings and adopt them as the mapping; read-only against Frontier's files and explicit -- nothing here runs on its own after the first enable."""
        binds = load_binds(journal_dir)
        if binds is None:
            return self.mapping
        devices = self.monitor.devices if self.monitor is not None else ()
        mapping = resolve_mapping(binds, devices, load_device_mappings())
        self.apply_mapping(mapping)
        return mapping

    def apply_mapping(self, mapping: FlightMapping) -> None:
        self.mapping = mapping
        self.tracker.rebind(mapping)
        self.config.flight_mapping = mapping.to_config()
        self._dirty = True

    def ensure_boost_binding(self, journal_dir=None) -> bool:
        """Backfill a boost button into a mapping saved before boost support; only the boost button is merged (existing axes untouched, so a manual remap is never clobbered). Returns whether config changed."""
        if self.mapping.boost is not None or self.mapping.is_empty:
            return False
        binds = load_binds(journal_dir)
        if binds is None or binds.boost is None:
            return False
        devices = self.monitor.devices if self.monitor is not None else ()
        resolved = resolve_mapping(binds, devices, load_device_mappings())
        if resolved.boost is None:
            return False
        self.mapping = replace(self.mapping, boost=resolved.boost)
        self.tracker.mapping = self.mapping
        self.config.flight_mapping = self.mapping.to_config()
        return True

    def reresolve(self) -> None:
        """Re-pin the saved mapping after a hotplug, by device id."""
        self.tracker.rebind(self.mapping)
        self._dirty = True

    def set_scale(self, scale: float) -> None:
        self.config.gizmo_scale = scale
        for window in self.windows:
            window.apply_scale(scale)

    def set_font_pt(self, font_pt: int) -> None:
        """Follow the configured UI font size."""
        for window in self.windows:
            window.gizmo.set_font_pt(font_pt)

    def set_apply_deadzone(self, enabled: bool) -> None:
        self.config.gizmo_apply_deadzone = enabled
        self.tracker.apply_deadzones = enabled
        self._dirty = True

    #  event intake

    def _on_controller_event(self, event: ControllerEvent) -> None:
        changed = self.tracker.handle(event)
        if self._is_boost_press(event) and self.boost.boost():
            changed = True
        if changed:
            self._dirty = True

    def _is_boost_press(self, event: ControllerEvent) -> bool:
        button = self.mapping.boost
        return (
            button is not None
            and not event.initial
            and event.kind == "button"
            and event.value != 0
            and event.device_id == button.device_id
            and event.index == button.index
        )

    def handle_journal_event(self, event: Mapping[str, object]) -> None:
        """Fold a journal event in; visibility follows on the next tick."""
        if event.get("event") == "Loadout":
            self.boost.set_ship(read_loadout(event))
            self._dirty = True
        if self.flight_state.handle(event):
            self._dirty = True
            self.refresh_visibility()

    def seed_docked(self, docked: bool) -> None:
        """Take the docked state from the engine's replayed AppState; only live events reach ``handle_journal_event``, so without this the gizmos would start out believing they're flying."""
        self.flight_state.seed_docked(docked)
        self.refresh_visibility()

    def set_journal_dir(self, journal_dir: object) -> None:
        """Point at the journal directory and seed the current ship from it; the boost readout needs the current ship, which arrives only in a Loadout (replayed before we listen), so the newest is read now."""
        self._journal_dir = Path(journal_dir) if journal_dir else None
        # Mappings saved before boost support lack the boost button; backfill it now that we have the binds path and live devices.
        self.ensure_boost_binding(self._journal_dir)
        if self._journal_dir is not None:
            loadout = latest_loadout(self._journal_dir)
            if loadout is not None:
                self.boost.set_ship(read_loadout(loadout))
                self._dirty = True

    def set_game_focused(self, focused: bool) -> None:
        """Track game focus: pass the mouse through only while focused; alt-tabbing away is what makes the gizmos draggable."""
        self._game_focused = focused
        for window in self.windows:
            window.set_click_through(focused and self.config.auto_click_through)
        # A window manager restacks the game over us when it takes focus, and the overlay reasserts at the same moment -- so do we, or we end up underneath it.
        if focused:
            for window in self.windows:
                window.assert_above()

    #  visibility

    @property
    def should_show(self) -> bool:
        if not self.config.gizmo_enabled:
            return False
        if self.config.gizmo_in_flight_only:
            return self.flight_state.in_flight
        return True

    def refresh_visibility(self) -> None:
        show = self.should_show
        for window in self.windows:
            if show and not window.isVisible():
                window.show()
                window.assert_above()
            elif not show and window.isVisible():
                window.hide()
        # Painting a hidden window is wasted work, so the clock only runs when something is actually on screen.
        if show and not self._timer.isActive():
            self._last_tick = None  # avoid a huge dt after being hidden
            self._timer.start()
        elif not show and self._timer.isActive():
            self._timer.stop()

    def _tick(self) -> None:
        now = time.monotonic()
        dt = 0.0 if self._last_tick is None else now - self._last_tick
        self._last_tick = now
        self.boost.advance(dt)
        boost_state = self.boost.state

        if not self._dirty and boost_state == self._last_boost_state:
            return
        self._dirty = False
        self._last_boost_state = boost_state
        state = self.tracker.state
        self.thrust.gizmo.set_state(state)
        self.thrust.gizmo.set_boost_state(boost_state)
        self.rotation.gizmo.set_state(state)

    def stop(self) -> None:
        self._timer.stop()
        for window in self.windows:
            window.hide()
        self.hide_targets()
