"""The single task-list entry every other HUD window hangs off; on Wayland every top-level window is listed separately and ``Qt.Tool`` can't ask to be skipped as on X11, so the overlay, its collapsed emblem, and each gizmo would each claim a slot -- this window exists to be the *one* entry the dock shows, staying mapped all session as a normal (non-``Tool``) window with every other HUD window reparented onto it as a transient child the compositor keeps out of the list."""

# SPDX-License-Identifier: GPL-3.0-or-later

from __future__ import annotations

from PySide6.QtCore import QEvent, Qt, Signal
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QWidget


class DockAnchor(QWidget):
    """A near-invisible, always-mapped window that owns the app's dock slot."""

    activated = Signal()

    def __init__(self, icon: QIcon) -> None:
        super().__init__()
        self.setWindowTitle("EDSC")
        self.setWindowIcon(icon)
        # A *normal* window type (no Qt.Tool) is what earns the single dock slot; frameless + translucent + one pixel keeps it from ever showing.
        self.setWindowFlags(Qt.FramelessWindowHint)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setAttribute(Qt.WA_TransparentForMouseEvents)
        # Map it without stealing focus from the game at launch; it must be mapped for a Wayland child to bind onto it as a transient parent.
        self.setAttribute(Qt.WA_ShowWithoutActivating)
        self.resize(1, 1)

    def adopt(self, window: QWidget) -> None:
        """Make ``window`` a transient child so the dock stops listing it; the window keeps its own flags (frameless, tool, keep-above), only the parent-child link is added. Reparenting hides the window, so callers must adopt each one *before* it is first shown."""
        window.setParent(self, window.windowFlags())

    def changeEvent(self, event: QEvent) -> None:
        super().changeEvent(event)
        # Clicking the lone dock entry activates this otherwise-empty surface; read that as "bring the overlay back" rather than leaving it inert.
        if event.type() == QEvent.Type.ActivationChange and self.isActiveWindow():
            self.activated.emit()
