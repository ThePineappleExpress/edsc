"""Small reusable widgets shared by the overlay and settings dialog."""

# SPDX-License-Identifier: GPL-3.0-or-later

from __future__ import annotations

from collections.abc import Callable

from PySide6.QtCore import QPoint, QSize, Qt, QTimer
from PySide6.QtGui import QMouseEvent
from PySide6.QtWidgets import (
    QFrame,
    QLabel,
    QSizePolicy,
    QTableView,
    QToolButton,
    QWidget,
)

from . import theme

# How long a transient status notice stays up before the previous text returns.
_FLASH_MS = 2500


def tool_button(text: str, tip: str, checkable: bool = False) -> QToolButton:
    """A compact tool button with a pointing-hand cursor and tooltip."""
    button = QToolButton()
    button.setText(text)
    button.setToolTip(tip)
    button.setCheckable(checkable)
    button.setCursor(Qt.PointingHandCursor)
    return button


def flash_status(label: QLabel, notice: str) -> None:
    """Show a transient notice on a status label, then restore its text."""
    previous = label.text()
    label.setText(notice)

    def restore() -> None:
        # Skip if a search or another notice replaced this one meanwhile.
        if label.text() == notice:
            label.setText(previous)

    # ``label`` is the context object, so closing the overlay inside the notice window cancels the restore instead of calling it on a deleted widget.
    QTimer.singleShot(_FLASH_MS, label, restore)


class DragBar(QFrame):
    """Move a window by dragging this header strip."""

    def __init__(
        self,
        window: QWidget,
        *,
        on_release: Callable[[], None] | None = None,
    ) -> None:
        super().__init__()
        self._window = window
        self._on_release = on_release
        self._press_offset: QPoint | None = None
        self.setCursor(Qt.SizeAllCursor)

    def mousePressEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.LeftButton:
            self._press_offset = (
                event.globalPosition().toPoint()
                - self._window.frameGeometry().topLeft()
            )

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        if self._press_offset is not None and event.buttons() & Qt.LeftButton:
            self._window.move(
                event.globalPosition().toPoint() - self._press_offset
            )

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        self._press_offset = None
        if self._on_release is not None:
            self._on_release()


class FittedTable(QTableView):
    """Report content height while remaining free to expand or scroll."""

    def __init__(self) -> None:
        super().__init__()
        self.cap = theme.METRICS.table_height_cap
        self.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Expanding)

    def _content_height(self) -> int:
        model = self.model()
        rows = model.rowCount() if model else 0
        row_height = self.verticalHeader().defaultSectionSize()
        header = self.horizontalHeader()
        header_height = header.height() or header.sizeHint().height()
        return header_height + max(1, rows) * row_height + 2 * self.frameWidth()

    def sizeHint(self) -> QSize:
        return QSize(
            super().sizeHint().width(),
            min(self._content_height(), self.cap),
        )

    def minimumSizeHint(self) -> QSize:
        row_height = self.verticalHeader().defaultSectionSize()
        header_height = self.horizontalHeader().sizeHint().height()
        return QSize(0, header_height + row_height)


class ResultsTable(FittedTable):
    """Allocate spare width between a priority name and a flexible column."""

    def __init__(self) -> None:
        super().__init__()
        self.fixed_widths: dict[int, int] = {}
        self.flex_col = 1
        self.flex_min = theme.METRICS.station_system_width_fallback
        self.priority_min = theme.METRICS.station_name_width_fallback

    def relayout_columns(self) -> None:
        if not self.fixed_widths or self.model() is None:
            return
        header = self.horizontalHeader()
        for column, width in self.fixed_widths.items():
            header.resizeSection(column, width)

        available = self.viewport().width() - sum(self.fixed_widths.values())
        slack = theme.METRICS.station_column_slack
        priority_full = max(0, self.sizeHintForColumn(0)) + slack
        flex_full = max(0, self.sizeHintForColumn(self.flex_col)) + slack
        flex_width = max(
            self.flex_min,
            min(flex_full, available - priority_full),
        )
        priority_width = max(self.priority_min, available - flex_width)
        if priority_width + flex_width > available:
            flex_width = max(0, available - priority_width)
        header.resizeSection(self.flex_col, flex_width)
        header.resizeSection(0, priority_width)

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self.relayout_columns()


class ElideLabel(QLabel):
    """Elide long text instead of forcing the parent window wider."""

    def __init__(self) -> None:
        super().__init__()
        self._full_text = ""

    def setText(self, text: str) -> None:
        self._full_text = text
        self.setToolTip(text)
        self._relide()

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._relide()

    def _relide(self) -> None:
        text = self.fontMetrics().elidedText(
            self._full_text,
            Qt.ElideRight,
            max(0, self.contentsRect().width()),
        )
        super().setText(text)

    def minimumSizeHint(self) -> QSize:
        return QSize(0, super().minimumSizeHint().height())
