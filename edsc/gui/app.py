"""Application bootstrap: QApplication, engine, overlay, and the tray icon.


    EDSC - Colonization commodities tracker
    Copyright (C) 2026  ThePineappleExpress

    This program is free software: you can redistribute it and/or modify
    it under the terms of the GNU General Public License as published by
    the Free Software Foundation, either version 3 of the License, or
    (at your option) any later version.

    This program is distributed in the hope that it will be useful,
    but WITHOUT ANY WARRANTY; without even the implied warranty of
    MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
    GNU General Public License for more details.

    You should have received a copy of the GNU General Public License
    along with this program.  If not, see <https://www.gnu.org/licenses/>.

"""

from __future__ import annotations

import os
import sys


def _prefer_xcb() -> None:
    if sys.platform == "win32":
        return
    if os.environ.get("QT_QPA_PLATFORM"):
        return
    if os.environ.get("DISPLAY"):
        os.environ["QT_QPA_PLATFORM"] = "xcb"


_prefer_xcb()

from PySide6.QtGui import QAction, QIcon
from PySide6.QtWidgets import QApplication, QMenu, QSystemTrayIcon

from .. import core, hud_colors
from ..config import Config
from ..engine import Engine
from ..journal.locator import find_journal_dir
from ..paths import asset_path
from ..platform.hotkeys import GlobalHotkeys
from . import theme
from .overlay import OverlayWindow
from .settings_dialog import SettingsDialog


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

        self.config = Config.load()
        self._apply_hud_colours()
        self.engine = Engine(self.config)
        self.overlay = OverlayWindow(self.config)

        self.overlay.settings_requested.connect(self.open_settings)
        self.overlay.quit_requested.connect(self.quit)
        self.overlay.carrier_changed.connect(self._save_state)
        self.overlay.project_removed.connect(self._save_state)
        self.engine.state_changed.connect(self._on_state_changed)
        self.engine.status_changed.connect(self.overlay.set_status)

        # Global hotkeys so tab switching works even while the game is focused.
        # The combos are only grabbed while the game window actually has focus,
        # so they aren't stolen from other applications (Ctrl+Shift+arrows is
        # word-selection almost everywhere).
        self.hotkeys = GlobalHotkeys(self.qapp)
        self.hotkeys.bind("Ctrl+Shift+Left", self.overlay.select_prev_tab)
        self.hotkeys.bind("Ctrl+Shift+Right", self.overlay.select_next_tab)
        if self.overlay.focus_detection_available:
            self.overlay.game_focus_changed.connect(self.hotkeys.set_active)
        else:
            # No way to tell when the game is focused: keep the old always-on
            # behaviour rather than losing the hotkeys entirely.
            self.hotkeys.set_active(True)

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

    #  actions

    def _apply_hud_colours(self) -> None:
        """Rebuild every GUI style from the player's HUD colours and settings."""
        journal_dir = find_journal_dir(self.config.journal_dir or None)
        theme.apply_hud_matrix(hud_colors.load_matrix(journal_dir))
        theme.apply_application_theme(self.qapp, self.config.font_point_size)

    def toggle_overlay(self) -> None:
        if self.overlay.isVisible():
            self.overlay.hide()
        else:
            self.overlay.show()
            self.overlay.raise_()

    def open_settings(self) -> None:
        dialog = SettingsDialog(self.config, self.overlay)
        if dialog.exec():
            prev_dir = self.config.journal_dir
            dialog.apply_to(self.config)
            self.config.save()
            # The theme owns every application-level palette and style rule;
            # rebuild it for both HUD-colour and font-size changes.
            self._apply_hud_colours()
            if self.config.journal_dir != prev_dir:
                # The graphics config lives in the same game install / Proton
                # prefix as the journals, so the theme was re-resolved above.
                self.engine.stop()
                self.engine.start()
            self.overlay.apply_appearance()
            self.overlay.sync_from_config()

    def _on_state_changed(self) -> None:
        self.overlay.refresh(self.engine.state)

    def _save_state(self) -> None:
        try:
            core.save_state(self.engine.state)
        except OSError:
            pass

    def reset_carrier(self) -> None:
        self.engine.state.carrier_cargo.clear()
        self._save_state()
        self.overlay.refresh(self.engine.state)

    def quit(self) -> None:
        self.qapp.quit()

    def _shutdown(self) -> None:
        self.overlay.persist_geometry()
        self.overlay.stop()
        self.hotkeys.stop()
        self.config.save()
        self.engine.stop()

    #  run 

    def run(self) -> int:
        self.overlay.show()
        # start() emits state_changed for the cached state immediately, so the
        # overlay always renders something while history replays off-thread.
        self.engine.start()
        return self.qapp.exec()


def run() -> int:
    return Application().run()
