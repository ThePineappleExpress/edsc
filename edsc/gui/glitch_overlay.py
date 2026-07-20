"""Reusable whole-widget glitch and blackout rendering."""

# SPDX-License-Identifier: GPL-3.0-or-later

from __future__ import annotations

import random
from dataclasses import dataclass

from PySide6.QtCore import QEvent, QObject, QPoint, QRect, Qt
from PySide6.QtGui import QColor, QFont, QPainter, QPixmap
from PySide6.QtWidgets import QAbstractButton, QLabel, QTabBar, QWidget

DEFAULT_GLITCH_CYCLE_MS = 760


@dataclass(frozen=True)
class TextFragment:
    """Visible text captured in coordinates relative to an effect target."""

    rect: QRect
    text: str
    font: QFont
    flags: int


def collect_text_fragments(root: QWidget) -> tuple[TextFragment, ...]:
    """Collect visible widget text without resizing or rasterising the glyphs; the overlay redraws these fragments in their original rectangles and fonts, so corruption never scales or distorts the source text."""
    fragments: list[TextFragment] = []
    for label in root.findChildren(QLabel):
        if label.text() and label.isVisibleTo(root):
            flags = label.alignment().value
            if label.wordWrap():
                flags |= Qt.TextWordWrap.value
            fragments.append(_fragment(root, label, label.text(), flags))

    for button in root.findChildren(QAbstractButton):
        if button.text() and button.isVisibleTo(root):
            fragments.append(
                _fragment(root, button, button.text(), Qt.AlignCenter.value)
            )

    tab_bar = root.findChild(QTabBar)
    if tab_bar is not None and tab_bar.isVisibleTo(root):
        origin = tab_bar.mapTo(root, QPoint())
        for index in range(tab_bar.count()):
            fragments.append(
                TextFragment(
                    tab_bar.tabRect(index).translated(origin),
                    tab_bar.tabText(index),
                    tab_bar.font(),
                    Qt.AlignCenter.value,
                )
            )
    return tuple(fragments)


def _fragment(
    root: QWidget,
    widget: QWidget,
    text: str,
    flags: int,
) -> TextFragment:
    return TextFragment(
        QRect(widget.mapTo(root, QPoint()), widget.size()),
        text,
        widget.font(),
        flags,
    )


class GlitchOverlay(QWidget):
    """Opaque canvas for reusable glitch, blackout, and restore effects; follows its parent widget's geometry -- call :meth:`start_effect`, then drive via :meth:`set_effect`/:meth:`set_progress`/:meth:`advance_glitch`. Generic phases are ``flicker``, ``glitch``, ``blackout`` and ``restore``; unknown phases stay black unless a subclass paints them in :meth:`_paint_custom_effect`."""

    def __init__(self, parent: QWidget) -> None:
        super().__init__(parent)
        self.setAttribute(Qt.WA_OpaquePaintEvent)
        self.setFocusPolicy(Qt.StrongFocus)
        self.setGeometry(parent.rect())
        self.hide()

        self.phase = "idle"
        self.progress = 0.0
        self.snapshot = QPixmap()
        self.fragments: tuple[TextFragment, ...] = ()
        self._rng = random.Random()
        parent.installEventFilter(self)

    def capture(self) -> None:
        """Capture the parent and its text while keeping this canvas out of it."""
        source = self.parentWidget()
        if source is None:
            self.snapshot = QPixmap()
            self.fragments = ()
            return

        was_visible = self.isVisible()
        if was_visible:
            self.hide()
        self.snapshot = source.grab()
        self.fragments = collect_text_fragments(source)
        self.setGeometry(source.rect())
        if was_visible:
            self.show()
            self.raise_()

    def start_effect(self, phase: str, progress: float = 0.0) -> None:
        """Capture the current interface and show an effect above it."""
        self.capture()
        self.set_effect(phase, progress)
        self.show()
        self.raise_()

    def set_effect(self, phase: str, progress: float = 0.0) -> None:
        """Select an effect frame without taking ownership of animation time."""
        self.phase = phase
        self.set_progress(progress)

    def set_progress(self, progress: float) -> None:
        """Set a normalised animation position and schedule a repaint."""
        self.progress = max(0.0, min(1.0, progress))
        self.update()

    def advance_glitch(
        self,
        elapsed_ms: int,
        cycle_ms: int = DEFAULT_GLITCH_CYCLE_MS,
    ) -> None:
        """Advance a looping glitch by elapsed animation time."""
        if cycle_ms <= 0:
            raise ValueError("cycle_ms must be greater than zero")
        self.set_progress((self.progress + (elapsed_ms / cycle_ms)) % 1.0)

    def clear_effect(self) -> None:
        """Hide the canvas and return it to an inert state."""
        self.hide()
        self.phase = "idle"
        self.progress = 0.0
        self.snapshot = QPixmap()
        self.fragments = ()
        self.update()

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:
        if watched is self.parentWidget() and event.type() in {
            QEvent.Resize,
            QEvent.Show,
        }:
            parent = self.parentWidget()
            if parent is not None:
                self.setGeometry(parent.rect())
                if self.isVisible():
                    self.raise_()
        return super().eventFilter(watched, event)

    def paintEvent(self, event) -> None:
        del event
        painter = QPainter(self)
        painter.fillRect(self.rect(), QColor(0, 0, 0))
        painter.setRenderHint(QPainter.SmoothPixmapTransform)

        if self.phase == "flicker":
            self._paint_flicker(painter)
        elif self.phase == "glitch":
            self._paint_glitch(painter)
        elif self.phase == "restore":
            self._paint_restore(painter)
        elif self.phase not in {"idle", "blackout"}:
            self._paint_custom_effect(painter)

    def _paint_custom_effect(self, painter: QPainter) -> None:
        """Paint a subclass-specific phase after the black base is drawn."""
        del painter

    def _paint_glitch(self, painter: QPainter) -> None:
        """Loop corruption at a readable but unstable strength."""
        pulse = 1.0 - abs((2.0 * self.progress) - 1.0)
        self._paint_flicker(painter, progress=0.32 + (0.42 * pulse))

    def _paint_flicker(
        self, painter: QPainter, *, progress: float | None = None
    ) -> None:
        progress = self.progress if progress is None else progress
        survival = max(0.0, 1.0 - progress) ** 1.25
        if not self.snapshot.isNull() and self._rng.random() < survival:
            opacity = survival * self._rng.uniform(0.28, 1.0)
            painter.setOpacity(opacity)
            painter.drawPixmap(0, 0, self.snapshot)

            # Displaced scanline slices break images and chrome apart without scaling the source snapshot or its text.
            for _ in range(7):
                strip_h = self._rng.randint(2, 18)
                y = self._rng.randrange(max(1, self.height()))
                displacement = self._rng.randint(-24, 24)
                target = QRect(0, y, self.width(), strip_h)
                painter.save()
                painter.setClipRect(target)
                painter.setOpacity(opacity * self._rng.uniform(0.35, 1.0))
                painter.drawPixmap(displacement, 0, self.snapshot)
                painter.restore()

        painter.setOpacity(1.0)
        for fragment in self.fragments:
            if self._rng.random() > survival:
                continue
            rect = fragment.rect.intersected(self.rect())
            if rect.isEmpty():
                continue
            painter.fillRect(rect.adjusted(-2, -1, 2, 1), QColor(0, 0, 0))
            painter.setFont(fragment.font)
            painter.setPen(
                QColor(
                    self._rng.randint(100, 205),
                    self._rng.randint(205, 255),
                    self._rng.randint(20, 100),
                )
            )
            painter.drawText(rect, fragment.flags, self._scramble(fragment.text))

    def _paint_restore(self, painter: QPainter) -> None:
        """Reveal the untouched screen in-place from top to bottom."""
        if self.snapshot.isNull():
            return
        line_height = max(2, min(12, self.height() // 80))
        reveal_height = min(self.height(), int(self.height() * self.progress))
        for y in range(0, reveal_height, line_height):
            height = min(line_height, reveal_height - y)
            painter.save()
            painter.setClipRect(0, y, self.width(), height)
            painter.setOpacity(self._rng.uniform(0.35, 1.0))
            painter.drawPixmap(0, 0, self.snapshot)
            painter.restore()

    def _scramble(self, text: str) -> str:
        alphabet = "ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789!@#$%&?<>/\\"
        return "".join(
            char if char.isspace() else self._rng.choice(alphabet)
            for char in text
        )
