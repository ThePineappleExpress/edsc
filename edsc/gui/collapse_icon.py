"""The floating emblem the overlay collapses into."""

# SPDX-License-Identifier: GPL-3.0-or-later

from __future__ import annotations

from collections.abc import Callable

from PySide6.QtCore import QPoint, Qt
from PySide6.QtGui import QMouseEvent
from PySide6.QtWidgets import (
    QApplication,
    QGraphicsOpacityEffect,
    QLabel,
    QWidget,
)

from ..config import Config
from . import icons, theme


class CollapsedIcon(QWidget):
    """The tinted app emblem the overlay collapses into; a frameless icon-sized window -- drag it anywhere (position persists), click to bring the overlay back."""

    def __init__(self, config: Config, *, on_restore: Callable[[], None]) -> None:
        super().__init__()
        self._config = config
        self._on_restore = on_restore
        self._press_offset: QPoint | None = None
        self._dragged = False
        self.setWindowTitle("EDSC")
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setCursor(Qt.PointingHandCursor)
        self.setToolTip("EDSC - click to restore the overlay")
        px = theme.METRICS.collapsed_icon_px
        self.setFixedSize(px, px)
        self._label = QLabel(self)
        self._label.setFixedSize(px, px)
        self.opacity_effect = QGraphicsOpacityEffect(self._label)
        self.opacity_effect.setOpacity(1.0)
        self._label.setGraphicsEffect(self.opacity_effect)
        self.apply_appearance()
        self.apply_flags()

    def apply_appearance(self) -> None:
        """(Re)render the glyph so it picks up HUD recolours."""
        self._label.setPixmap(
            icons.app_glyph_pixmap(theme.METRICS.collapsed_icon_px)
        )

    def apply_flags(self) -> None:
        """Follow the overlay's keep-above setting (re-shows if visible)."""
        flags = Qt.FramelessWindowHint | Qt.Tool
        if self._config.always_on_top:
            flags |= Qt.WindowStaysOnTopHint
        visible = self.isVisible()
        self.setWindowFlags(flags)
        if visible:
            self.show()

    #  drag to move, click to restore

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

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        if self._press_offset is None:
            return
        self._press_offset = None
        if self._dragged:
            self._config.collapsed_x = self.x()
            self._config.collapsed_y = self.y()
        else:
            self._on_restore()
