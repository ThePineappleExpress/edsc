"""Bootstrap the Qt application, engine, overlay, and tray icon."""

# SPDX-License-Identifier: GPL-3.0-or-later

from __future__ import annotations

import sys
from contextlib import suppress

from PySide6.QtGui import QAction, QIcon
from PySide6.QtWidgets import QApplication, QMenu, QSystemTrayIcon, QWidget

from .. import core, hud_colors
from ..config import Config
from ..controller_bindings import action_for_event
from ..eddn import EddnSender, EddnUplink
from ..engine import Engine
from ..flight_state import FlightStateTracker
from ..journal.locator import find_journal_dir
from ..paths import asset_path
from ..platform.controller import ControllerMonitor
from ..platform.hotkeys import GlobalHotkeys
from . import theme
from .dock_anchor import DockAnchor
from .gizmo_window import GizmoController
from .overlay import OverlayWindow
from .settings_dialog import SettingsDialog
from .thargoid_effects import ThargoidEffectController


def _app_icon() -> QIcon:
    return QIcon(str(asset_path("icon.png")))


class Application:
    """Owns the long-lived objects and wires them together."""

    def __init__(self) -> None:
        self.qapp = QApplication.instance() or QApplication(sys.argv)
        self.qapp.setApplicationName("EDSC")
        self.qapp.setQuitOnLastWindowClosed(False)
        self.icon = _app_icon()
        self.qapp.setWindowIcon(self.icon)

        # A Wayland compositor lists every top-level window separately, and the HUD's tool windows (overlay, emblem, gizmos) can't ask to be skipped there as on X11; this lone mapped window is the one dock entry the app should own, every other HUD window becoming its transient child (see _adopt_hud_windows) and dropping off the list.
        self.anchor = DockAnchor(self.icon)
        self.anchor.activated.connect(self._reveal_overlay)
        self.anchor.show()

        self.config = Config.load()
        self._apply_hud_colours()
        self.engine = Engine(self.config)
        # The live EDDN relay, created only while the user has consented; wired to the engine so live journal/market events fan out to it.
        self.eddn_uplink: EddnUplink | None = None
        self._sync_eddn_uplink()
        self.overlay = OverlayWindow(self.config)
        self.thargoid_effects = ThargoidEffectController(self.overlay)
        self._flight_state = FlightStateTracker()
        # The overlay and its collapsed emblem are separate top-level windows; cover both so whichever representation is visible shares the state.
        self.thargoid_effects.add_target(self.overlay.collapse_icon)

        self.overlay.settings_requested.connect(self.open_settings)
        self.overlay.quit_requested.connect(self.quit)
        self.overlay.carrier_changed.connect(self._save_state)
        self.overlay.project_removed.connect(self._save_state)
        self.engine.state_changed.connect(self._on_state_changed)
        self.engine.status_changed.connect(self.overlay.set_status)
        self.engine.ready.connect(self._on_journal_ready)
        self.engine.live_event.connect(self._on_live_event)
        self.engine.live_event.connect(self.thargoid_effects.handle_event)

        # Global hotkeys so tab switching and collapse work even while the game is focused; grabbed only while the game window has focus so they aren't stolen from other apps (Ctrl+Shift+arrows is word-selection almost everywhere).
        self.hotkeys = GlobalHotkeys(self.qapp)
        self.hotkeys.bind("Ctrl+Shift+Left", self.overlay.select_prev_tab)
        self.hotkeys.bind("Ctrl+Shift+Right", self.overlay.select_next_tab)
        self.hotkeys.bind("Ctrl+Shift+Down", self.overlay.toggle_collapsed)
        if self.overlay.focus_detection_available:
            self.overlay.game_focus_changed.connect(self.hotkeys.set_active)
        else:
            # No way to tell when the game is focused: keep the old always-on behaviour rather than losing the hotkeys entirely.
            self.hotkeys.set_active(True)

        # Controller capture stays process-wide; configured button/hat edges invoke the same narrow action API as keyboard shortcuts.
        self._controller_bindings_suspended = False
        self._controller_full_opacity = False
        self.controllers = ControllerMonitor(self.qapp)
        self.controllers.event_received.connect(self._on_controller_event)
        self.controllers.start()

        # Flight gizmos: separate always-on-top decals fed by the same monitor.
        self.gizmos = GizmoController(self.config, self.controllers, self.qapp)
        self.engine.live_event.connect(self.gizmos.handle_journal_event)
        if self.overlay.focus_detection_available:
            self.overlay.game_focus_changed.connect(self.gizmos.set_game_focused)
        # Fold every HUD window under the anchor before any is shown; refresh_visibility below is the gizmos' first show.
        self._adopt_hud_windows()
        self.gizmos.refresh_visibility()

        self.tray = self._build_tray()
        self.qapp.aboutToQuit.connect(self._shutdown)

    #  tray 

    def _build_tray(self) -> QSystemTrayIcon | None:
        if not QSystemTrayIcon.isSystemTrayAvailable():
            return None
        tray = QSystemTrayIcon(self.icon)
        tray.setToolTip("EDSC - Elite Dangerous Supply Chain")
        menu = QMenu()
        show_action = QAction("Show / hide overlay", menu)
        show_action.triggered.connect(self.toggle_overlay)
        settings_action = QAction("Settings…", menu)
        settings_action.triggered.connect(self.open_settings)
        reset_carrier_action = QAction("Reset fleet-carrier cargo", menu)
        reset_carrier_action.triggered.connect(self.reset_carrier)
        quit_action = QAction("Quit EDSC", menu)
        quit_action.triggered.connect(self.quit)
        menu.addAction(show_action)
        menu.addAction(settings_action)
        menu.addAction(reset_carrier_action)
        menu.addSeparator()
        menu.addAction(quit_action)
        tray.setContextMenu(menu)
        tray.activated.connect(self._on_tray_activated)
        tray.show()
        return tray

    def _on_tray_activated(self, reason) -> None:
        if reason == QSystemTrayIcon.Trigger:
            self.toggle_overlay()

    #  dock anchor

    def _adopt_hud_windows(self) -> None:
        """Reparent every HUD window onto the anchor so the dock lists one app; the overlay/emblem swap and the gizmos come and go, so no single HUD window is a stable parent -- the anchor is. Each keeps its own flags, only the transient-parent link is added, which is what keeps them out of the compositor's task list."""
        for window in (
            self.overlay,
            self.overlay.collapse_icon,
            self.gizmos.thrust,
            self.gizmos.rotation,
        ):
            self.anchor.adopt(window)

    def _reveal_overlay(self) -> None:
        """Bring the overlay back when the lone dock entry is activated."""
        if self.overlay.collapsed:
            self.overlay.set_collapsed(False)
        else:
            self.overlay.show()
            self.overlay.raise_()

    #  actions

    def _apply_hud_colours(self) -> None:
        """Rebuild every GUI style from the player's HUD colours and settings."""
        journal_dir = find_journal_dir(self.config.journal_dir or None)
        theme.apply_hud_matrix(hud_colors.load_matrix(journal_dir))
        theme.apply_application_theme(self.qapp, self.config.font_point_size)

    def toggle_overlay(self) -> None:
        if self.overlay.collapsed:
            self.overlay.set_collapsed(False)
        elif self.overlay.isVisible():
            self.overlay.hide()
        else:
            self.overlay.show()
            self.overlay.raise_()

    def open_settings(self) -> None:
        # Surface the live uplink's status and activity console when sharing is on; None makes the dialog fall back to a config-derived summary.
        uplink = getattr(self, "eddn_uplink", None)
        eddn_status = uplink.sender.status_line() if uplink is not None else None
        # Raw events still reach the dialog's recorder, but must not invoke the old live binding while the user is choosing a replacement.
        self._controller_bindings_suspended = True
        try:
            dialog = SettingsDialog(
                self.config,
                self.overlay,
                eddn_status=eddn_status,
                eddn_uplink=uplink,
                on_eddn_sync=self.engine.sync_eddn_now if uplink is not None else None,
                controllers=self.controllers,
            )
            effect_target = (
                dialog
                if isinstance(dialog, QWidget)
                and hasattr(self, "thargoid_effects")
                else None
            )
            if effect_target is not None:
                self.thargoid_effects.add_target(effect_target)
            # Show the draggable aim crosshairs while the Controls tab is open; they parent to the modal dialog so they stay grabbable over it.
            gizmos = getattr(self, "gizmos", None)
            if gizmos is not None and hasattr(dialog, "gizmo_targeting_changed"):
                dialog.gizmo_targeting_changed.connect(
                    lambda active, d=dialog: gizmos.show_targets(d)
                    if active
                    else gizmos.hide_targets()
                )
            try:
                accepted = bool(dialog.exec())
            finally:
                if effect_target is not None:
                    self.thargoid_effects.remove_target(effect_target)
                if gizmos is not None:
                    gizmos.hide_targets()
        finally:
            self._controller_bindings_suspended = False
        if accepted:
            prev_dir = self.config.journal_dir
            dialog.apply_to(self.config)
            self.config.save()
            # The theme owns every application-level palette and style rule; rebuild it for both HUD-colour and font-size changes.
            self._apply_hud_colours()
            if self.config.journal_dir != prev_dir:
                # The graphics config lives in the same game install / Proton prefix as the journals, so the theme was re-resolved above.
                self.thargoid_effects.reset()
                self.engine.stop()
                self.engine.start()
            # Create or tear down the relay to match the (possibly changed) sharing consent.
            self._sync_eddn_uplink()
            self.overlay.apply_appearance()
            self.overlay.sync_from_config()
            self._sync_overlay_opacity()
            self._sync_overlay_collapsed()
            self._sync_gizmos()

    def _sync_gizmos(self) -> None:
        """Apply gizmo settings, importing Elite's bindings on first enable."""
        self.gizmos.set_scale(self.config.gizmo_scale)
        self.gizmos.set_font_pt(self.config.font_point_size)
        self.gizmos.set_apply_deadzone(self.config.gizmo_apply_deadzone)
        # Nothing is mapped until the user first switches the gizmos on, so the import happens here rather than costing every launch a file read.
        if self.config.gizmo_enabled and self.gizmos.mapping.is_empty:
            self.gizmos.import_from_binds(self.engine.journal_dir)
            self.config.save()
        self.gizmos.set_journal_dir(self.engine.journal_dir)
        self.gizmos.refresh_visibility()

    def _sync_eddn_uplink(self) -> None:
        """Create or tear down the EDDN relay to match the sharing consent; sharing runs only with an explicit opt-in and a minted anonymous ID, and the relay is armed as soon as journal replay finishes (here if already done, else later from the engine's ready signal)."""
        want = bool(self.config.eddn_enabled) and bool(self.config.eddn_uploader_id)
        if want and self.eddn_uplink is None:
            sender = EddnSender(self.config.eddn_uploader_id)
            self.eddn_uplink = EddnUplink(sender)
            self.engine.uplink = self.eddn_uplink
            # If the engine already handed over its watcher, live events are flowing now; arm immediately rather than waiting for a restart.
            if self.engine.watcher is not None:
                self.eddn_uplink.arm()
        elif not want and self.eddn_uplink is not None:
            self.engine.uplink = None
            uplink, self.eddn_uplink = self.eddn_uplink, None
            uplink.sender.close()

    def _on_state_changed(self) -> None:
        self.overlay.refresh(self.engine.state)
        self._sync_overlay_opacity()
        self._sync_overlay_collapsed()
        self._sync_gizmo_docked()

    def _on_live_event(self, event: dict) -> None:
        """Follow Docked/Undocked transitions even when MarketID is absent."""
        tracker = getattr(self, "_flight_state", None)
        if tracker is None:
            return
        if tracker.handle(event):
            self._sync_overlay_opacity()
            self._sync_overlay_collapsed()

    def _replayed_docked_state(self) -> bool:
        """Best-effort docked state after replay, falling back to market id."""
        journal_dir = getattr(self.engine, "journal_dir", None)
        if journal_dir:
            tracker = FlightStateTracker()
            for event in core.read_session_events(journal_dir):
                tracker.handle(event)
            if tracker.status.docked is not None:
                return bool(tracker.status.docked)
        return self.engine.state.docked_market_id is not None

    def _is_docked(self) -> bool:
        """Current docked state for UI behavior (overlay + gizmo visibility)."""
        tracker = getattr(self, "_flight_state", None)
        if tracker is not None and tracker.status.docked is not None:
            return bool(tracker.status.docked)
        state = getattr(getattr(self, "engine", None), "state", None)
        return getattr(state, "docked_market_id", None) is not None

    def _on_journal_ready(self) -> None:
        """Seed live sequence detection after historical replay has finished."""
        self.thargoid_effects.reset(self.engine.state.current_system)
        if hasattr(self, "_flight_state"):
            self._flight_state.seed_docked(self._replayed_docked_state())
        self._sync_gizmo_docked()
        # The current ship + distributor and live pips live in the journal dir; hand it over so the boost readout can seed itself.
        self.gizmos.set_journal_dir(self.engine.journal_dir)

    def _sync_gizmo_docked(self) -> None:
        """Hand the replayed docked state to the gizmos; ``AppState`` learns this from the full replay, but the gizmos only see live events and can't work it out at startup."""
        self.gizmos.seed_docked(self._is_docked())

    def _sync_overlay_opacity(self) -> None:
        """Use the docked opacity exactly while the journal state is docked."""
        if getattr(self, "_controller_full_opacity", False):
            opacity = 1.0
        else:
            docked = self._is_docked()
            opacity = (
                self.config.docked_opacity
                if self.config.auto_opacity_on_dock and docked
                else self.config.overlay_opacity
            )
        self.overlay.set_opacity(opacity)

    def _toggle_controller_opacity(self) -> None:
        """Switch between full opacity and the currently configured opacity."""
        self._controller_full_opacity = not getattr(
            self, "_controller_full_opacity", False
        )
        self._sync_overlay_opacity()

    def _on_controller_event(self, event) -> None:
        """Invoke one configured action for a selected-device activation."""
        if getattr(self, "_controller_bindings_suspended", False):
            return
        if event.device_id != self.config.controller_device_id:
            return
        action_id = action_for_event(self.config.controller_bindings, event)
        actions = {
            "previous_tab": self.overlay.select_prev_tab,
            "next_tab": self.overlay.select_next_tab,
            "toggle_collapsed": self.overlay.toggle_collapsed,
            "toggle_opacity": self._toggle_controller_opacity,
            "refresh_search": self.overlay.refresh_current_search,
        }
        action = actions.get(action_id)
        if action is not None:
            action()

    def _sync_overlay_collapsed(self) -> None:
        """Follow docking state when automatic collapsing is enabled."""
        if not self.config.auto_collapse_on_undock:
            return
        docked = self._is_docked()
        self.overlay.set_collapsed(not docked)

    def _save_state(self) -> None:
        with suppress(OSError):
            core.save_state(self.engine.state)

    def reset_carrier(self) -> None:
        self.engine.state.carrier_cargo.clear()
        self._save_state()
        self.overlay.refresh(self.engine.state)

    def quit(self) -> None:
        self.qapp.quit()

    def _shutdown(self) -> None:
        self.overlay.persist_geometry()
        self.overlay.stop()
        self.gizmos.save_positions()
        self.gizmos.stop()
        self.hotkeys.stop()
        self.controllers.stop()
        self.thargoid_effects.reset()
        if self.eddn_uplink is not None:
            self.eddn_uplink.sender.close()
        self.anchor.close()
        self.config.save()
        self.engine.stop()

    #  run 

    def run(self) -> int:
        if self.config.collapsed:
            self.overlay.set_collapsed(True)
        else:
            self.overlay.show()
        # start() emits state_changed for the cached state immediately, so the overlay always renders something while history replays off-thread.
        self.engine.start()
        return self.qapp.exec()


def run() -> int:
    return Application().run()
