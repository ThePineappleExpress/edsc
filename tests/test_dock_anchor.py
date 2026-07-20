"""Tests for the dock anchor that folds the HUD into one task-list entry."""

# SPDX-License-Identifier: GPL-3.0-or-later


from PySide6.QtCore import QPoint, Qt
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QWidget

from edsc.gui.dock_anchor import DockAnchor


def _tool_window() -> QWidget:
    """A stand-in for a HUD window: frameless, keep-above, off the taskbar."""
    window = QWidget()
    window.setWindowFlags(
        Qt.FramelessWindowHint | Qt.Tool | Qt.WindowStaysOnTopHint
    )
    window.move(300, 200)
    return window


def test_anchor_is_a_normal_window_so_it_keeps_the_dock_slot(qapp):
    # Qt.Tool windows are what the compositor drops; the anchor must not be one, or there'd be no dock entry left at all.
    anchor = DockAnchor(QIcon())
    assert anchor.windowType() != Qt.WindowType.Tool
    assert anchor.windowType() == Qt.WindowType.Window


def test_adopt_makes_a_transient_child_without_disturbing_it(qapp):
    anchor = DockAnchor(QIcon())
    window = _tool_window()

    anchor.adopt(window)

    # Reparented onto the anchor, but still a top-level tool window at its spot.
    assert window.parent() is anchor
    assert window.isWindow()
    assert window.windowType() == Qt.WindowType.Tool
    assert bool(window.windowFlags() & Qt.WindowStaysOnTopHint)
    assert window.pos() == QPoint(300, 200)


def test_adopted_child_keeps_its_parent_across_a_flag_change(qapp):
    # Toggling always-on-top re-applies flags via setWindowFlags; the transient link must survive it, or the window pops back onto the dock.
    anchor = DockAnchor(QIcon())
    window = _tool_window()
    anchor.adopt(window)

    window.setWindowFlags(Qt.FramelessWindowHint | Qt.Tool)

    assert window.parent() is anchor
    assert window.isWindow()
