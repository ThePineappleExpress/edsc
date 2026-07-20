"""A wrapping button layout that justifies each row to the full width; Qt ships no flow layout, so the Colonize tab's toggle groups use this one. Items flow left-to-right and wrap when the next won't fit, then each row is widened equally to span the full available width -- so a wide window packs more buttons per row and whatever lands on a row stretches to fill it (a lone row of toggles fills the window rather than hugging the left edge)."""

# SPDX-License-Identifier: GPL-3.0-or-later

from __future__ import annotations

from PySide6.QtCore import QRect, QSize, Qt
from PySide6.QtWidgets import QLayout, QLayoutItem, QWidget


class FlowLayout(QLayout):
    """Left-to-right wrapping layout that stretches each row to the full width."""

    def __init__(self, parent: QWidget | None = None, spacing: int = 6) -> None:
        super().__init__(parent)
        self._items: list[QLayoutItem] = []
        self._spacing = spacing
        self.setContentsMargins(0, 0, 0, 0)

    #  QLayout plumbing

    def addItem(self, item: QLayoutItem) -> None:
        self._items.append(item)

    def count(self) -> int:
        return len(self._items)

    def itemAt(self, index: int) -> QLayoutItem | None:
        if 0 <= index < len(self._items):
            return self._items[index]
        return None

    def takeAt(self, index: int) -> QLayoutItem | None:
        if 0 <= index < len(self._items):
            return self._items.pop(index)
        return None

    def expandingDirections(self) -> Qt.Orientations:
        return Qt.Orientation(0)

    def hasHeightForWidth(self) -> bool:
        return True

    def heightForWidth(self, width: int) -> int:
        return self._arrange(QRect(0, 0, width, 0), apply=False)

    def setGeometry(self, rect: QRect) -> None:
        super().setGeometry(rect)
        self._arrange(rect, apply=True)

    def sizeHint(self) -> QSize:
        return self.minimumSize()

    def minimumSize(self) -> QSize:
        # One button wide is enough to be usable; height follows from the width.
        size = QSize()
        for item in self._items:
            size = size.expandedTo(item.minimumSize())
        m = self.contentsMargins()
        size += QSize(m.left() + m.right(), m.top() + m.bottom())
        return size

    #  the justified flow

    def _arrange(self, rect: QRect, apply: bool) -> int:
        """Pack items into rows, stretch each row to fill; return total height."""
        m = self.contentsMargins()
        avail = rect.width() - m.left() - m.right()
        spacing = self._spacing

        # Greedily partition items into rows by their natural (hint) widths.
        rows: list[tuple[list[QLayoutItem], int]] = []
        row: list[QLayoutItem] = []
        used = 0  # natural widths + inter-item spacing accumulated on this row
        for item in self._items:
            w = item.sizeHint().width()
            if row and used + spacing + w > avail:
                rows.append((row, used))
                row, used = [], 0
            used += (spacing if row else 0) + w
            row.append(item)
        if row:
            rows.append((row, used))

        # Place each row, widening its items equally to fill ``avail``; we drive widget geometry directly rather than via ``item.setGeometry`` so the stretch also applies to fixed-policy widgets like QToolButton, which a QWidgetItem would clamp back to their size hint.
        y = rect.y() + m.top()
        for items, used in rows:
            each, spare = divmod(max(0, avail - used), len(items))
            x = rect.x() + m.left()
            row_h = 0
            for i, item in enumerate(items):
                w = item.sizeHint().width() + each + (1 if i < spare else 0)
                h = item.sizeHint().height()
                if apply:
                    widget = item.widget()
                    (widget or item).setGeometry(QRect(x, y, w, h))
                x += w + spacing
                row_h = max(row_h, h)
            y += row_h + spacing

        if rows:
            y -= spacing
        return y - rect.y() + m.bottom()
