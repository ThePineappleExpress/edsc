
import pytest
from PySide6.QtCore import QPoint, QRect, Qt
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QLabel,
    QPushButton,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from edsc.gui.glitch_overlay import GlitchOverlay, collect_text_fragments


def _shown_target(qapp):
    target = QWidget()
    target.resize(360, 240)
    layout = QVBoxLayout(target)
    label = QLabel("Status text")
    button = QPushButton("Action")
    tabs = QTabWidget()
    tabs.addTab(QWidget(), "Cargo")
    layout.addWidget(label)
    layout.addWidget(button)
    layout.addWidget(tabs)
    target.show()
    qapp.processEvents()
    return target, label, button


def _dispose(widget, qapp):
    widget.close()
    widget.deleteLater()
    qapp.processEvents()


def test_collect_text_fragments_preserves_original_geometry_and_font(qapp):
    target, label, button = _shown_target(qapp)

    fragments = collect_text_fragments(target)
    by_text = {fragment.text: fragment for fragment in fragments}

    assert {"Status text", "Action", "Cargo"} <= by_text.keys()
    assert by_text["Status text"].rect == QRect(
        label.mapTo(target, QPoint()), label.size()
    )
    assert by_text["Status text"].font == label.font()
    assert by_text["Action"].rect == QRect(
        button.mapTo(target, QPoint()), button.size()
    )
    _dispose(target, qapp)


def test_start_effect_captures_and_exposes_global_phase_controls(qapp):
    target, _label, _button = _shown_target(qapp)
    overlay = GlitchOverlay(target)

    overlay.start_effect("glitch", 0.25)

    assert overlay.isVisible()
    assert overlay.geometry() == target.rect()
    assert overlay.snapshot.deviceIndependentSize().toSize() == target.size()
    assert overlay.phase == "glitch"
    assert overlay.progress == 0.25

    overlay.set_effect("blackout")
    qapp.processEvents()
    frame = overlay.grab().toImage()
    assert frame.pixelColor(frame.width() // 2, frame.height() // 2) == QColor(
        Qt.black
    )

    overlay.set_progress(2.0)
    assert overlay.progress == 1.0
    overlay.advance_glitch(190, 760)
    assert overlay.progress == pytest.approx(0.25)

    overlay.clear_effect()
    assert not overlay.isVisible()
    assert overlay.phase == "idle"
    assert overlay.snapshot.isNull()
    assert overlay.fragments == ()
    _dispose(target, qapp)


def test_visible_overlay_tracks_parent_resize(qapp):
    target, _label, _button = _shown_target(qapp)
    overlay = GlitchOverlay(target)
    overlay.start_effect("blackout")

    target.resize(480, 300)
    qapp.processEvents()

    assert overlay.geometry() == target.rect()
    _dispose(target, qapp)
