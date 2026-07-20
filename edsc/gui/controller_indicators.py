"""Visual diagnostics for raw controller input."""

# SPDX-License-Identifier: GPL-3.0-or-later

from __future__ import annotations

from PySide6.QtCore import QRectF, QSize, Qt
from PySide6.QtGui import QPainter, QPen
from PySide6.QtWidgets import QWidget

from ..platform.controller import hat_direction, normalize_axis
from . import theme

_AXIS_MIN = -32768
_AXIS_MAX = 32767


class AxisIndicator(QWidget):
    def __init__(self, index: int, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.index = index
        self.value = 0
        self.normalized = 0.0
        self.setAccessibleName(f"Axis {index}")
        self.setMinimumHeight(36)

    def sizeHint(self) -> QSize:
        return QSize(360, 38)

    def set_value(self, value: int) -> None:
        self.value = max(_AXIS_MIN, min(_AXIS_MAX, int(value)))
        self.normalized = normalize_axis(self.value)
        self.setAccessibleDescription(
            f"{self.value}, normalized {self.normalized:+.3f}"
        )
        self.update()

    def paintEvent(self, _event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        header_height = painter.fontMetrics().height()
        half_width = self.width() // 2

        painter.setPen(theme.ORANGE)
        painter.drawText(
            0,
            0,
            half_width,
            header_height,
            Qt.AlignLeft | Qt.AlignVCenter,
            f"Axis {self.index}",
        )
        painter.setPen(theme.TEXT_DIM)
        painter.drawText(
            half_width,
            0,
            self.width() - half_width,
            header_height,
            Qt.AlignRight | Qt.AlignVCenter,
            f"{self.value:+6d}   {self.normalized:+.3f}",
        )

        bar = QRectF(0, header_height + 6, max(1, self.width()), 8)
        painter.fillRect(bar, theme.GRID)
        centre = bar.center().x()
        value_x = centre + self.normalized * bar.width() / 2
        painter.fillRect(
            QRectF(
                min(centre, value_x),
                bar.top(),
                max(1.0, abs(value_x - centre)),
                bar.height(),
            ),
            theme.ORANGE,
        )
        painter.setPen(QPen(theme.TEXT, 1))
        painter.drawLine(
            int(centre),
            int(bar.top() - 2),
            int(centre),
            int(bar.bottom() + 2),
        )


class ButtonIndicator(QWidget):
    def __init__(self, index: int, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.index = index
        self.pressed = False
        self.setAccessibleName(f"Button {index}")
        self.setAccessibleDescription("Released")
        self.setMinimumSize(self.sizeHint())

    def sizeHint(self) -> QSize:
        text_width = self.fontMetrics().horizontalAdvance(f"B{self.index}")
        return QSize(
            max(38, text_width + 16),
            max(28, self.fontMetrics().height() + 10),
        )

    def set_pressed(self, pressed: bool) -> None:
        self.pressed = bool(pressed)
        self.setAccessibleDescription("Pressed" if self.pressed else "Released")
        self.update()

    def paintEvent(self, _event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        area = QRectF(self.rect()).adjusted(1, 1, -1, -1)
        painter.setPen(QPen(theme.ORANGE if self.pressed else theme.GRID, 1))
        painter.setBrush(theme.ORANGE if self.pressed else theme.BG_TAB)
        painter.drawRoundedRect(area, 3, 3)
        painter.setPen(theme.BG if self.pressed else theme.ORANGE_DIM)
        painter.drawText(self.rect(), Qt.AlignCenter, f"B{self.index}")


class HatIndicator(QWidget):
    def __init__(self, index: int, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.index = index
        self.value = 0
        self.direction = "Centered"
        self.setAccessibleName(f"Hat {index}")
        self.setMinimumHeight(30)

    def sizeHint(self) -> QSize:
        return QSize(180, 32)

    def set_value(self, value: int) -> None:
        self.value = int(value) & 0x0F
        self.direction = hat_direction(self.value)
        self.setAccessibleDescription(self.direction)
        self.update()

    def paintEvent(self, _event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        area = QRectF(self.rect()).adjusted(1, 1, -1, -1)
        active = self.value != 0
        painter.setPen(QPen(theme.ORANGE if active else theme.GRID, 1))
        painter.setBrush(theme.BG_ACTIVE if active else theme.BG_TAB)
        painter.drawRoundedRect(area, 3, 3)
        painter.setPen(theme.ORANGE if active else theme.ORANGE_DIM)
        painter.drawText(self.rect(), Qt.AlignCenter, f"Hat {self.index}: {self.direction}")
