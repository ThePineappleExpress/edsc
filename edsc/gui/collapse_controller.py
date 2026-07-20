"""Animating the overlay between its full panel and the floating icon."""

# SPDX-License-Identifier: GPL-3.0-or-later

from __future__ import annotations

from collections.abc import Callable

from PySide6.QtCore import (
    QAbstractAnimation,
    QEasingCurve,
    QObject,
    QParallelAnimationGroup,
    QPoint,
    QPropertyAnimation,
    QRect,
    QSequentialAnimationGroup,
    QSize,
)
from PySide6.QtWidgets import QGraphicsOpacityEffect, QLayout, QWidget

from ..config import Config
from ..platform import topmost
from . import theme
from .collapse_icon import CollapsedIcon


class CollapseController(QObject):
    """Drive the overlay's collapse/restore transition; owns the animation state machine, the two opacity effects, and the authoritative expanded geometry. The overlay's widgets/models are never rebuilt -- at the end of a collapse the native window is merely hidden, so state keeps updating without repainting an invisible panel and restore stays fast. The panel background is kept separate from its contents so a collapse first fades the readable UI without squeezing its text, then shrinks the empty shell to a square; that needs one opacity effect on the content and another on the panel, but Qt can't paint two nested effects (an ancestor's effect re-enters the same paint device and drops the inner one), so the two are never live at once -- each animation hands over to the next (see ``_begin_shell_fade``/``_begin_content_fade``)."""

    def __init__(
        self,
        window: QWidget,
        panel: QWidget,
        content: QWidget,
        icon: CollapsedIcon,
        config: Config,
        *,
        game_focused: Callable[[], bool],
    ) -> None:
        super().__init__(window)
        self._window = window
        self._content = content
        self._icon = icon
        self._config = config
        self._game_focused = game_focused
        self._animation: QSequentialAnimationGroup | None = None
        self._transition_active = False
        self._expanded_geometry: QRect | None = None
        self._transition_minimum_size = QSize()
        self._transition_layout_constraint = QLayout.SetDefaultConstraint

        self.content_opacity = self._opacity_effect(content)
        self.panel_opacity = self._opacity_effect(panel)

    @staticmethod
    def _opacity_effect(target: QWidget) -> QGraphicsOpacityEffect:
        effect = QGraphicsOpacityEffect(target)
        effect.setOpacity(1.0)
        effect.setEnabled(False)
        target.setGraphicsEffect(effect)
        return effect

    #  state

    @property
    def collapsed(self) -> bool:
        """Whether the overlay is currently collapsed into the floating icon."""
        return self._config.collapsed

    @property
    def transition_active(self) -> bool:
        """Whether a collapse/restore is mid-flight (geometry is not real)."""
        return self._transition_active

    @property
    def animation(self) -> QSequentialAnimationGroup | None:
        """The in-flight animation group, or None when settled."""
        return self._animation

    #  geometry

    def persist_geometry(self) -> None:
        """Save the overlay's rect, ignoring in-animation geometries."""
        if self._transition_active:
            return
        self._config.overlay_x = self._window.x()
        self._config.overlay_y = self._window.y()
        self._config.overlay_width = self._window.width()
        self._config.overlay_height = self._window.height()
        self._expanded_geometry = QRect(self._window.geometry())

    def note_geometry(self) -> None:
        """Adopt the window's current rect as the expanded one."""
        self._expanded_geometry = QRect(self._window.geometry())

    def expanded_rect(self) -> QRect:
        if self._expanded_geometry is not None:
            return QRect(self._expanded_geometry)
        return QRect(
            int(self._config.overlay_x),
            int(self._config.overlay_y),
            int(self._config.overlay_width),
            int(self._config.overlay_height),
        )

    def _capture_expanded_geometry(self) -> None:
        """Remember the real overlay rect, never an in-animation geometry."""
        if self._transition_active:
            return
        self._expanded_geometry = QRect(self._window.geometry())
        self.persist_geometry()

    def _collapsed_position(self) -> QPoint:
        if (
            self._config.collapsed_x is not None
            and self._config.collapsed_y is not None
        ):
            return QPoint(
                int(self._config.collapsed_x), int(self._config.collapsed_y)
            )
        geometry = self._expanded_geometry or self._window.geometry()
        return geometry.topLeft()

    def _collapsed_rect(self) -> QRect:
        px = theme.METRICS.collapsed_icon_px
        return QRect(self._collapsed_position(), QSize(px, px))

    #  transition bookkeeping

    def _prepare_transition(self) -> None:
        """Temporarily let the top-level layout clip down to icon size."""
        if self._transition_active:
            return
        self._transition_active = True
        layout = self._window.layout()
        self._transition_minimum_size = self._window.minimumSize()
        self._transition_layout_constraint = layout.sizeConstraint()
        layout.setSizeConstraint(QLayout.SetNoConstraint)
        self._window.setMinimumSize(0, 0)

    def _finish_transition(self) -> None:
        if not self._transition_active:
            return
        self._window.layout().setSizeConstraint(
            self._transition_layout_constraint
        )
        self._window.setMinimumSize(self._transition_minimum_size)
        self._transition_active = False

    def _cancel_animation(self) -> None:
        animation = self._animation
        self._animation = None
        if animation is not None:
            animation.stop()
            animation.deleteLater()

    @staticmethod
    def _property_animation(
        target,
        property_name: bytes,
        start,
        end,
        duration: int,
        easing: QEasingCurve.Type = QEasingCurve.InOutCubic,
    ) -> QPropertyAnimation:
        animation = QPropertyAnimation(target, property_name)
        animation.setStartValue(start)
        animation.setEndValue(end)
        animation.setDuration(duration)
        animation.setEasingCurve(easing)
        return animation

    def _run_animation(
        self, group: QSequentialAnimationGroup, finished: Callable[[], None]
    ) -> None:
        self._animation = group

        def complete() -> None:
            if self._animation is not group:
                return
            self._animation = None
            group.deleteLater()
            finished()

        group.finished.connect(complete)
        group.start()

    #  effect handover (the two effects must never be live together)

    @staticmethod
    def _on_start(animation: QPropertyAnimation, slot: Callable[[], None]) -> None:
        """Run ``slot`` the moment ``animation`` becomes the running one."""
        animation.stateChanged.connect(
            lambda new, _old: slot() if new == QAbstractAnimation.Running else None
        )

    def _begin_shell_fade(self) -> None:
        """Collapse: the content has faded out, so hand over to the panel; the content is already invisible and the window already icon-sized, so hiding it here costs nothing on screen and lets the content's effect go away before the panel's turns on."""
        self._content.hide()
        self.content_opacity.setEnabled(False)
        self.panel_opacity.setEnabled(True)

    def _begin_content_fade(self) -> None:
        """Restore: the shell is back, so hand over to the content; the panel is fully opaque by now, so dropping its effect is invisible."""
        self.panel_opacity.setEnabled(False)
        self._content.show()
        self.content_opacity.setEnabled(True)

    def _show_icon(self, opacity: float) -> None:
        self._icon.move(self._collapsed_position())
        self._icon.opacity_effect.setOpacity(opacity)
        self._icon.show()
        self._icon.raise_()
        if self._game_focused() and self._config.always_on_top:
            topmost.assert_above(self._icon)

    #  public transitions

    def set_collapsed(self, collapsed: bool) -> None:
        """Animate between the full overlay and its floating icon."""
        collapsed = bool(collapsed)
        if collapsed == self._config.collapsed and self._animation is None:
            already_settled = (
                collapsed and self._window.isHidden() and self._icon.isVisible()
            ) or (
                not collapsed
                and self._window.isVisible()
                and self._icon.isHidden()
            )
            if already_settled:
                return
        self._config.collapsed = collapsed
        if collapsed:
            # Nothing useful to animate at startup or while hidden to tray; settle directly into the icon in those cases.
            if not self._window.isVisible() and not self._transition_active:
                self._capture_expanded_geometry()
                self._settle_collapsed()
            else:
                self._start_collapse_animation()
        else:
            self._start_restore_animation()

    def toggle(self) -> None:
        self.set_collapsed(not self._config.collapsed)

    def assert_topmost(self) -> None:
        """Re-assert keep-above for whichever of the pair is on screen."""
        topmost.assert_above(self._window)
        if self._icon.isVisible():
            topmost.assert_above(self._icon)

    def stop(self) -> None:
        """Abandon any transition and release the icon (called on shutdown)."""
        self._cancel_animation()
        if self._transition_active:
            self._window.hide()
            self._window.setGeometry(self.expanded_rect())
            self._reset_effects()
            self._finish_transition()
        self._icon.close()

    #  animations

    def _start_collapse_animation(self) -> None:
        reversing = self._transition_active
        self._cancel_animation()
        if not reversing:
            self._capture_expanded_geometry()
        self._prepare_transition()

        start = QRect(self._window.geometry())
        square = self._collapsed_rect()
        short = QRect(start.x(), start.y(), start.width(), square.height())
        # Only the content's effect is live for the fade and the reshaping; the panel's joins for the final shell fade, once this one is gone.
        self._content.show()
        self.content_opacity.setEnabled(True)
        self.panel_opacity.setEnabled(False)
        icon_opacity = (
            self._icon.opacity_effect.opacity()
            if self._icon.isVisible()
            else 0.0
        )
        self._show_icon(icon_opacity)

        metrics = theme.METRICS
        sequence = QSequentialAnimationGroup(self)
        sequence.addAnimation(
            self._property_animation(
                self.content_opacity,
                b"opacity",
                self.content_opacity.opacity(),
                0.0,
                metrics.collapse_content_fade_ms,
                QEasingCurve.OutCubic,
            )
        )
        sequence.addAnimation(
            self._property_animation(
                self._window,
                b"geometry",
                start,
                short,
                metrics.collapse_height_ms,
            )
        )
        sequence.addAnimation(
            self._property_animation(
                self._window,
                b"geometry",
                short,
                square,
                metrics.collapse_width_ms,
            )
        )
        sequence.addAnimation(
            self._property_animation(
                self._icon.opacity_effect,
                b"opacity",
                self._icon.opacity_effect.opacity(),
                1.0,
                metrics.collapse_icon_fade_ms,
                QEasingCurve.OutCubic,
            )
        )
        shell_fade = self._property_animation(
            self.panel_opacity,
            b"opacity",
            self.panel_opacity.opacity(),
            0.0,
            metrics.collapse_shell_fade_ms,
            QEasingCurve.OutCubic,
        )
        self._on_start(shell_fade, self._begin_shell_fade)
        sequence.addAnimation(shell_fade)
        self._run_animation(sequence, self._settle_collapsed)

    def _start_restore_animation(self) -> None:
        self._cancel_animation()
        self._prepare_transition()

        expanded = self.expanded_rect()
        if not self._window.isVisible():
            self._window.setGeometry(self._collapsed_rect())
            self.content_opacity.setOpacity(0.0)
            self.panel_opacity.setOpacity(0.0)
        start = QRect(self._window.geometry())
        wide = QRect(expanded.x(), expanded.y(), expanded.width(), start.height())
        # The shell fades in and grows empty; the content's effect joins for the final fade, once the panel's is gone.
        self._content.hide()
        self.content_opacity.setEnabled(False)
        self.panel_opacity.setEnabled(True)
        self._show_icon(self._icon.opacity_effect.opacity())
        self._window.show()
        self._window.raise_()

        metrics = theme.METRICS
        sequence = QSequentialAnimationGroup(self)
        crossfade = QParallelAnimationGroup()
        crossfade.addAnimation(
            self._property_animation(
                self.panel_opacity,
                b"opacity",
                self.panel_opacity.opacity(),
                1.0,
                metrics.collapse_icon_fade_ms,
                QEasingCurve.OutCubic,
            )
        )
        crossfade.addAnimation(
            self._property_animation(
                self._icon.opacity_effect,
                b"opacity",
                self._icon.opacity_effect.opacity(),
                0.0,
                metrics.collapse_icon_fade_ms,
                QEasingCurve.OutCubic,
            )
        )
        sequence.addAnimation(crossfade)
        sequence.addAnimation(
            self._property_animation(
                self._window,
                b"geometry",
                start,
                wide,
                metrics.collapse_width_ms,
            )
        )
        sequence.addAnimation(
            self._property_animation(
                self._window,
                b"geometry",
                wide,
                expanded,
                metrics.collapse_height_ms,
            )
        )
        content_fade = self._property_animation(
            self.content_opacity,
            b"opacity",
            self.content_opacity.opacity(),
            1.0,
            metrics.collapse_content_fade_ms,
            QEasingCurve.OutCubic,
        )
        self._on_start(content_fade, self._begin_content_fade)
        sequence.addAnimation(content_fade)
        self._run_animation(sequence, self._settle_expanded)

    #  settling

    def _reset_effects(self) -> None:
        """Settle back to a plain, effect-free tree (the handover hides content)."""
        self._content.show()
        self.content_opacity.setOpacity(1.0)
        self.content_opacity.setEnabled(False)
        self.panel_opacity.setOpacity(1.0)
        self.panel_opacity.setEnabled(False)

    def _settle_collapsed(self) -> None:
        """End collapsed but keep the complete, live overlay tree in memory."""
        self._window.hide()
        self._window.setGeometry(self.expanded_rect())
        self._reset_effects()
        self._icon.opacity_effect.setOpacity(1.0)
        self._show_icon(1.0)
        self._finish_transition()

    def _settle_expanded(self) -> None:
        self._window.setGeometry(self.expanded_rect())
        self._reset_effects()
        self._icon.hide()
        self._icon.opacity_effect.setOpacity(1.0)
        self._finish_transition()
        self.persist_geometry()
        self._window.show()
        self._window.raise_()
        if self._game_focused() and self._config.always_on_top:
            topmost.assert_above(self._window)
