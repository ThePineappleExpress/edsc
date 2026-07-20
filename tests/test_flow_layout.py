"""Tests for the justified wrapping FlowLayout."""

# SPDX-License-Identifier: GPL-3.0-or-later

from PySide6.QtCore import QRect, QSize
from PySide6.QtWidgets import QSizePolicy, QWidget

from edsc.gui.flow_layout import FlowLayout


class _Box(QWidget):
    """A pinned-hint widget with a *fixed* size policy, mirroring QToolButton; the fixed policy is deliberate -- a plain QWidgetItem would clamp it back to its hint, so these boxes prove the layout stretches rows regardless."""

    def __init__(self, width: int, height: int = 20) -> None:
        super().__init__()
        self._hint = QSize(width, height)
        self.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)

    def sizeHint(self) -> QSize:
        return self._hint

    def minimumSizeHint(self) -> QSize:
        return self._hint


def _panel(count=4, btn_width=60, spacing=6):
    host = QWidget()
    flow = FlowLayout(host, spacing=spacing)
    boxes = []
    for _ in range(count):
        box = _Box(btn_width)
        flow.addWidget(box)
        boxes.append(box)
    return host, flow, boxes


def test_single_row_stretches_to_fill_width(qapp):
    _host, flow, boxes = _panel(count=2, btn_width=60, spacing=6)
    flow.setGeometry(QRect(0, 0, 400, 100))
    # Two boxes, no wrap: they fill 400px minus the single 6px gap between.
    assert boxes[0].x() == 0
    right_edge = boxes[-1].x() + boxes[-1].width()
    assert right_edge == 400
    # Widened equally: both boxes end up the same width.
    assert boxes[0].width() == boxes[1].width()


def test_wraps_when_row_is_full(qapp):
    _host, flow, boxes = _panel(count=4, btn_width=60, spacing=6)
    # Only room for two 60px boxes + one gap (126px) per row.
    flow.setGeometry(QRect(0, 0, 130, 100))
    ys = [b.y() for b in boxes]
    assert ys[0] == ys[1]  # first two share a row
    assert ys[2] == ys[3]  # next two wrap to the second row
    assert ys[2] > ys[0]


def test_wider_window_packs_more_per_row(qapp):
    _host, flow, boxes = _panel(count=4, btn_width=60, spacing=6)
    flow.setGeometry(QRect(0, 0, 800, 100))
    # Plenty of room: all four land on one row that fills the width.
    assert len({b.y() for b in boxes}) == 1
    assert boxes[-1].x() + boxes[-1].width() == 800


def test_height_for_width_grows_as_it_wraps(qapp):
    _host, flow, _boxes = _panel(count=4, btn_width=60, spacing=6)
    tall = flow.heightForWidth(130)  # forces two rows
    short = flow.heightForWidth(800)  # single row
    assert tall > short
