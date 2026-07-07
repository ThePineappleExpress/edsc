"""Application bootstrap: QApplication, engine, overlay, and the tray icon."""

from __future__ import annotations

import os
import sys


def _prefer_xcb() -> None:
    if sys.platform in ("win32", "darwin"):
        return
    if os.environ.get("QT_QPA_PLATFORM"):
        return
    if os.environ.get("DISPLAY"):
        os.environ["QT_QPA_PLATFORM"] = "xcb"


_prefer_xcb()

from PySide6.QtCore import Qt
from PySide6.QtGui import QAction, QColor, QIcon, QPainter, QPixmap
from PySide6.QtWidgets import QApplication, QMenu, QSystemTrayIcon

from .. import core
from ..config import Config
from ..engine import Engine
from ..platform.hotkeys import GlobalHotkeys
from . import theme
from .overlay import OverlayWindow
from .settings_dialog import SettingsDialog


def _app_icon() -> QIcon:
    pm = QPixmap(64, 64)
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing)
    p.setPen(Qt.NoPen)
    p.setBrush(theme.ORANGE)
    p.drawEllipse(6, 6, 52, 52)
    p.setBrush(QColor(12, 14, 18))
    p.drawEllipse(18, 18, 28, 28)
    p.end()
    return QIcon(pm)


class Application:
    """Owns the long-lived objects and wires them together."""

    def __init__(self) -> None:
        self.qapp = QApplication.instance() or QApplication(sys.argv)
        self.qapp.setApplicationName("EDSC")
        self.qapp.setQuitOnLastWindowClosed(False)
        self.icon = _app_icon()
        self.qapp.setWindowIcon(self.icon)

        self.config = Config.load()
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
            self.overlay.apply_appearance()
            self.overlay.sync_from_config()
            if self.config.journal_dir != prev_dir:
                self.engine.stop()
                self.engine.start()

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
