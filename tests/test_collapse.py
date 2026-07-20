"""Tests for collapsing the overlay into (and out of) its floating icon."""

# SPDX-License-Identifier: GPL-3.0-or-later


import pytest
from PySide6.QtCore import QEvent, QPoint, QPointF, QRect, QSize, Qt
from PySide6.QtGui import QMouseEvent
from PySide6.QtTest import QTest

from edsc.config import Config
from edsc.gui.overlay import OverlayWindow


@pytest.fixture
def overlay(qapp):
    window = OverlayWindow(Config())
    window._auto_fit = False
    yield window
    window.stop()
    window.deleteLater()
    qapp.processEvents()


def _settle(overlay, qapp):
    """Skip to the end of the in-flight transition instead of watching it."""
    animation = overlay._collapse.animation
    if animation is None:
        return
    animation.setCurrentTime(max(0, animation.totalDuration() - 1))
    QTest.qWait(30)
    qapp.processEvents()
    assert overlay._collapse.animation is None


def _icon_mouse(icon, kind, pos, buttons=Qt.LeftButton):
    return QMouseEvent(
        kind,
        QPointF(pos),
        QPointF(icon.mapToGlobal(pos)),
        Qt.LeftButton,
        buttons,
        Qt.NoModifier,
    )


#  the round trip


def test_collapsing_swaps_the_overlay_for_the_icon(overlay, qapp):
    overlay.show()

    overlay.set_collapsed(True)
    _settle(overlay, qapp)

    assert overlay.isHidden()
    assert not overlay.collapse_icon.isHidden()
    assert overlay.collapsed is True
    assert overlay.config.collapsed is True

    overlay.set_collapsed(False)
    _settle(overlay, qapp)

    assert not overlay.isHidden()
    assert overlay.collapse_icon.isHidden()
    assert overlay.collapsed is False


def test_the_header_button_collapses_and_the_shortcut_restores(overlay, qapp):
    overlay.show()

    overlay.collapse_btn.click()
    _settle(overlay, qapp)
    assert overlay.config.collapsed is True

    overlay.toggle_collapsed()
    _settle(overlay, qapp)
    assert overlay.config.collapsed is False


def test_collapsing_while_hidden_settles_without_animating(overlay):
    """At startup or in the tray there is nothing on screen worth animating."""
    overlay.set_collapsed(True)

    assert overlay._collapse.animation is None
    assert not overlay.collapse_icon.isHidden()
    assert overlay.config.collapsed is True


def test_a_settled_state_is_not_re_animated(overlay, qapp):
    overlay.show()
    overlay.set_collapsed(True)
    _settle(overlay, qapp)

    overlay.set_collapsed(True)  # already collapsed

    assert overlay._collapse.animation is None


#  the animation itself


def test_the_collapse_fades_the_text_away_before_reshaping_the_window(overlay, qapp):
    overlay.resize(540, 420)
    overlay.show()
    qapp.processEvents()
    start = overlay.geometry()

    overlay.set_collapsed(True)

    sequence = overlay._collapse.animation
    assert sequence is not None
    assert sequence.animationCount() == 5
    content_fade = sequence.animationAt(0)
    height_shrink = sequence.animationAt(1)
    width_shrink = sequence.animationAt(2)
    icon_fade = sequence.animationAt(3)
    shell_fade = sequence.animationAt(4)
    px = overlay.collapse_icon.width()

    assert content_fade.targetObject() is overlay._collapse.content_opacity
    assert content_fade.endValue() == 0.0
    assert height_shrink.targetObject() is overlay
    assert height_shrink.endValue() == QRect(start.x(), start.y(), start.width(), px)
    assert width_shrink.targetObject() is overlay
    assert width_shrink.endValue().size() == QSize(px, px)
    assert icon_fade.targetObject() is overlay.collapse_icon.opacity_effect
    assert icon_fade.endValue() == 1.0
    assert shell_fade.targetObject() is overlay._collapse.panel_opacity
    assert shell_fade.endValue() == 0.0

    # At the first stage boundary all readable content is gone but the window hasn't changed shape yet, so text never appears compressed.
    sequence.setCurrentTime(content_fade.duration())
    assert overlay._collapse.content_opacity.opacity() == 0.0
    assert overlay.geometry() == start

    sequence.setCurrentTime(content_fade.duration() + height_shrink.duration())
    assert overlay.geometry() == height_shrink.endValue()
    sequence.setCurrentTime(
        content_fade.duration() + height_shrink.duration() + width_shrink.duration()
    )
    assert overlay.geometry() == width_shrink.endValue()

    _settle(overlay, qapp)


def _nesting_samples(overlay, sequence, step=5):
    """Every point in the sequence where both opacity effects are live."""
    collapse = overlay._collapse
    nested = []
    for t in range(0, sequence.totalDuration() + 1, step):
        sequence.setCurrentTime(t)
        if collapse.content_opacity.isEnabled() and collapse.panel_opacity.isEnabled():
            nested.append(t)
    return nested


@pytest.mark.parametrize("collapsing", [True, False])
def test_the_two_opacity_effects_are_never_live_together(overlay, qapp, collapsing):
    """Qt can't paint nested effects -- an ancestor's drops the inner one, so content/panel effects hand over between stages instead of overlapping; both at once silently loses the content fade and the text gets squeezed by the reshape."""
    overlay.show()
    qapp.processEvents()
    if not collapsing:  # start from the collapsed end, then measure the restore
        overlay.set_collapsed(True)
        _settle(overlay, qapp)

    overlay.set_collapsed(collapsing)

    sequence = overlay._collapse.animation
    assert sequence is not None
    assert _nesting_samples(overlay, sequence) == []
    _settle(overlay, qapp)


def test_the_content_is_gone_before_the_shell_fade_begins(overlay, qapp):
    overlay.show()
    qapp.processEvents()
    overlay.set_collapsed(True)
    sequence = overlay._collapse.animation
    shell_fade = sequence.animationAt(4)

    sequence.setCurrentTime(sequence.totalDuration() - shell_fade.duration())

    assert overlay.content.isHidden()
    assert overlay._collapse.panel_opacity.isEnabled()
    assert not overlay._collapse.content_opacity.isEnabled()
    _settle(overlay, qapp)


def test_a_settled_collapse_leaves_the_content_measurable(overlay, qapp):
    """The handover hides the content, but settling must put it back: refresh() runs while collapsed and _fit_height measures sizeHint(), so a still-hidden content widget would collapse that hint, resize the hidden window, and persist it as the *expanded* geometry."""
    overlay.show()
    overlay.set_collapsed(True)
    _settle(overlay, qapp)

    assert not overlay.content.isHidden()


def test_a_restore_brings_the_content_back(overlay, qapp):
    overlay.show()
    overlay.set_collapsed(True)
    _settle(overlay, qapp)

    overlay.set_collapsed(False)
    _settle(overlay, qapp)

    assert not overlay.content.isHidden()
    assert overlay._collapse.content_opacity.opacity() == 1.0
    assert not overlay._collapse.content_opacity.isEnabled()


def test_restoring_returns_to_the_pre_collapse_geometry(overlay, qapp):
    overlay.show()
    overlay.setGeometry(120, 140, 500, 400)
    overlay.persist_geometry()
    expanded = overlay.geometry()

    overlay.set_collapsed(True)
    _settle(overlay, qapp)
    overlay.set_collapsed(False)
    _settle(overlay, qapp)

    assert overlay.geometry() == expanded


def test_an_interrupted_collapse_does_not_persist_a_mid_animation_size(overlay, qapp):
    """Geometry is saved constantly; an in-flight square must never win."""
    overlay.show()
    overlay.setGeometry(120, 140, 500, 400)
    overlay.persist_geometry()

    overlay.set_collapsed(True)
    # Freeze the wall-clock-driven animation and step it to just before the end: deterministically mid-transition however slow the machine, with the shell genuinely squeezed (a real qWait here raced the animation finishing on a stalled runner, persisting the collapsed square).
    animation = overlay._collapse.animation
    animation.pause()
    animation.setCurrentTime(animation.totalDuration() - 1)
    assert overlay.width() != 500  # the squeezed rect that must not be saved
    overlay.persist_geometry()

    assert overlay.config.overlay_width == 500
    assert overlay.config.overlay_height == 400
    animation.resume()
    _settle(overlay, qapp)


#  the live tree survives


def test_a_collapsed_overlay_keeps_its_live_ui_tree(overlay, qapp):
    """State keeps updating while collapsed, so restoring is instant."""
    panel, content, status = overlay.panel, overlay.content, overlay.status_label

    overlay.set_collapsed(True)
    overlay.set_status("Updated while collapsed")

    assert overlay.isHidden()
    assert status.text() == "Updated while collapsed"

    overlay.set_collapsed(False)
    _settle(overlay, qapp)

    assert overlay.panel is panel
    assert overlay.content is content
    assert overlay.status_label is status
    assert overlay.status_label.text() == "Updated while collapsed"


#  the icon


def test_the_icon_appears_over_the_overlay_until_a_position_is_saved(overlay, qapp):
    overlay.move(100, 120)
    overlay.set_collapsed(True)

    assert overlay.collapse_icon.pos() == QPoint(100, 120)
    assert overlay.config.collapsed_x is None

    overlay.config.collapsed_x = 300
    overlay.config.collapsed_y = 40
    overlay.set_collapsed(False)
    _settle(overlay, qapp)
    overlay.set_collapsed(True)
    _settle(overlay, qapp)

    assert overlay.collapse_icon.pos() == QPoint(300, 40)


def test_clicking_the_icon_restores_the_overlay(overlay, qapp):
    overlay.set_collapsed(True)

    QTest.mouseClick(overlay.collapse_icon, Qt.LeftButton)
    _settle(overlay, qapp)

    assert overlay.config.collapsed is False
    assert not overlay.isHidden()
    assert overlay.collapse_icon.isHidden()


def test_dragging_the_icon_moves_it_and_persists_without_restoring(overlay):
    overlay.set_collapsed(True)
    icon = overlay.collapse_icon
    start = icon.pos()

    icon.mousePressEvent(_icon_mouse(icon, QEvent.MouseButtonPress, QPoint(10, 10)))
    icon.mouseMoveEvent(_icon_mouse(icon, QEvent.MouseMove, QPoint(50, 60)))
    icon.mouseReleaseEvent(
        _icon_mouse(icon, QEvent.MouseButtonRelease, QPoint(50, 60), Qt.NoButton)
    )

    assert icon.pos() == start + QPoint(40, 50)
    assert overlay.config.collapsed is True  # a drag is not a click
    assert (overlay.config.collapsed_x, overlay.config.collapsed_y) == (
        icon.x(),
        icon.y(),
    )


#  shutdown


def test_stopping_mid_transition_leaves_no_running_animation(overlay, qapp):
    overlay.show()
    overlay.set_collapsed(True)
    assert overlay._collapse.animation is not None

    overlay.stop()

    assert overlay._collapse.animation is None
    assert overlay.collapse_icon.isHidden()
