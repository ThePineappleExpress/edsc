"""Hidden Anti-Xeno sequence for the About page."""

# SPDX-License-Identifier: GPL-3.0-or-later

from __future__ import annotations

import time

from PySide6.QtCore import (
    QObject,
    QRectF,
    Qt,
    QTimer,
    QUrl,
    Signal,
)
from PySide6.QtGui import (
    QColor,
    QDesktopServices,
    QKeyEvent,
    QMouseEvent,
    QPainter,
    QPixmap,
)
from PySide6.QtWidgets import (
    QDialog,
    QLabel,
    QMessageBox,
    QPlainTextEdit,
    QWidget,
)

from ..paths import asset_path
from . import theme
from .glitch_overlay import GlitchOverlay

ANTI_XENO_URL = "https://antixenoinitiative.com/"


def _war_copy() -> str:
    """Read the bundled Anti-Xeno briefing shown at the end of the sequence."""
    try:
        return asset_path("war.md").read_text(encoding="utf-8").strip()
    except OSError:
        return ""


class ClickableImage(QLabel):
    """A pixmap label that reports completed left-button clicks."""

    clicked = Signal()

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        if (
            event.button() == Qt.LeftButton
            and self.rect().contains(event.position().toPoint())
        ):
            self.clicked.emit()
            event.accept()
            return
        super().mouseReleaseEvent(event)


class _AboutTakeover(GlitchOverlay):
    """Opaque canvas masking the settings panel during the sequence."""

    axi_clicked = Signal()

    def __init__(self, parent: QWidget) -> None:
        super().__init__(parent)
        self.thargoid = QPixmap(str(asset_path("thargoid.png")))
        self.antixeno = QPixmap(str(asset_path("antixeno.png")))
        self._axi_interactive = False
        self.axi_copy = QPlainTextEdit(self)
        self.axi_copy.setAccessibleName("Thargoid war history")
        self.axi_copy.setPlainText(_war_copy())
        self.axi_copy.setReadOnly(True)
        self.axi_copy.setUndoRedoEnabled(False)
        self.axi_copy.setLineWrapMode(QPlainTextEdit.WidgetWidth)
        self.axi_copy.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.axi_copy.setStyleSheet(theme.anti_xeno_briefing_stylesheet())
        self.axi_copy.hide()

    def set_axi_interactive(self, interactive: bool) -> None:
        self._axi_interactive = interactive
        self.setCursor(
            Qt.PointingHandCursor if interactive else Qt.ArrowCursor
        )

    def set_axi_content_visible(self, visible: bool) -> None:
        self.axi_copy.setVisible(visible)
        if visible:
            self.axi_copy.verticalScrollBar().setValue(0)
        self._layout_axi_content()

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        if (
            self._axi_interactive
            and event.button() == Qt.LeftButton
            and self._axi_rect().contains(event.position())
        ):
            self.axi_clicked.emit()
            event.accept()
            return
        event.accept()

    def keyPressEvent(self, event: QKeyEvent) -> None:
        # In particular, don't let Escape reject the settings dialog while the takeover is running.
        event.accept()

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._layout_axi_content()

    def _paint_custom_effect(self, painter: QPainter) -> None:
        if self.phase == "hydra":
            self._paint_hydra(painter)
        elif self.phase == "message_one":
            self._paint_message(painter, "They come for you...")
        elif self.phase == "message_two":
            self._paint_message(painter, "...we come for them!")
        elif self.phase in {"axi_fade", "axi_wait"}:
            opacity = self.progress if self.phase == "axi_fade" else 1.0
            logo = self._axi_rect()
            self._paint_pixmap(
                painter,
                self.antixeno,
                logo.center().x(),
                logo.center().y(),
                logo.width(),
                opacity=opacity,
            )

    def _paint_message(self, painter: QPainter, text: str) -> None:
        fade = min(1.0, self.progress / 0.22, (1.0 - self.progress) / 0.22)
        font = theme.resized_font(
            painter.font(),
            max(18, int(min(self.width(), self.height()) * 0.055)),
        )
        font.setBold(True)
        painter.setFont(font)
        painter.setOpacity(max(0.0, fade))
        painter.setPen(QColor(215, 242, 180))
        painter.drawText(
            self.rect().adjusted(24, 24, -24, -24),
            Qt.AlignCenter.value | Qt.TextWordWrap.value,
            text,
        )
        painter.setOpacity(1.0)

    def _paint_hydra(self, painter: QPainter) -> None:
        progress = self.progress * self.progress * (3.0 - 2.0 * self.progress)
        base = min(self.width(), self.height())
        size = base * (0.30 + 1.18 * progress)
        start_x = -base * 0.22
        start_y = self.height() + base * 0.22
        end_x = self.width() + base * 0.72
        end_y = -base * 0.72
        self._paint_pixmap(
            painter,
            self.thargoid,
            start_x + ((end_x - start_x) * progress),
            start_y + ((end_y - start_y) * progress),
            size,
            rotation=-50.0 + (250.0 * progress),
        )

    @staticmethod
    def _paint_pixmap(
        painter: QPainter,
        pixmap: QPixmap,
        center_x: float,
        center_y: float,
        size: float,
        *,
        rotation: float = 0.0,
        opacity: float = 1.0,
    ) -> None:
        if pixmap.isNull() or size <= 0:
            return
        scale = min(size / pixmap.width(), size / pixmap.height())
        width = pixmap.width() * scale
        height = pixmap.height() * scale
        painter.save()
        painter.setOpacity(max(0.0, min(1.0, opacity)))
        painter.translate(center_x, center_y)
        painter.rotate(rotation)
        painter.drawPixmap(
            QRectF(-width / 2.0, -height / 2.0, width, height),
            pixmap,
            QRectF(pixmap.rect()),
        )
        painter.restore()

    def _axi_margin(self) -> int:
        return min(32, max(16, min(self.width(), self.height()) // 25))

    def _axi_rect(self) -> QRectF:
        margin = self._axi_margin()
        # Preserve the About page's exact 256px logo scale whenever the window has room, but reserve a usable scrolling reading area.
        copy_height = min(160, max(96, self.height() // 3))
        size = min(
            theme.METRICS.about_icon_px,
            max(0, self.width() - (2 * margin)),
            max(0, self.height() - copy_height - (3 * margin)),
        )
        return QRectF(
            (self.width() - size) / 2.0,
            margin,
            size,
            size,
        )

    def _layout_axi_content(self) -> None:
        logo = self._axi_rect()
        margin = self._axi_margin()
        top = int(logo.bottom()) + margin
        self.axi_copy.setGeometry(
            margin,
            top,
            max(0, self.width() - (2 * margin)),
            max(0, self.height() - top - margin),
        )


class AboutEasterEggController(QObject):
    """Count logo clicks and run the timed, reversible About takeover."""

    REQUIRED_CLICKS = 8
    FLICKER_SECONDS = 2.6
    VOID_SECONDS = 5.0
    HYDRA_SECONDS = 5.6
    AFTER_HYDRA_SECONDS = 2.0
    MESSAGE_SECONDS = 2.2
    AXI_FADE_SECONDS = 1.1
    RESTORE_SECONDS = 0.45
    TICK_MS = 33
    MAIN_GLITCH_CYCLE_MS = 760

    def __init__(
        self,
        dialog: QDialog,
        panel: QWidget,
        logo: ClickableImage,
    ) -> None:
        super().__init__(dialog)
        self._dialog = dialog
        self._panel = panel
        self._clicks = 0
        self._phase = "idle"
        self._phase_started = 0.0
        self._active = False
        self._prompt_open = False

        self._takeover = _AboutTakeover(panel)
        self._main_window = dialog.parentWidget()
        self._main_takeover = (
            _AboutTakeover(self._main_window)
            if self._main_window is not None
            else None
        )
        if self._main_takeover is not None:
            # The dialog remains the modal interaction surface; this canvas only paints the real app window behind it.
            self._main_takeover.setAttribute(Qt.WA_TransparentForMouseEvents)
        self._timer = QTimer(self)
        self._timer.setInterval(self.TICK_MS)
        self._timer.timeout.connect(self._tick)
        self._main_glitch_timer = QTimer(self)
        self._main_glitch_timer.setInterval(self.TICK_MS)
        self._main_glitch_timer.timeout.connect(self._advance_main_glitch)
        logo.clicked.connect(self._logo_clicked)
        self._takeover.axi_clicked.connect(self._ask_humanity)

    @property
    def active(self) -> bool:
        return self._active

    @property
    def phase(self) -> str:
        return self._phase

    def _logo_clicked(self) -> None:
        if self._active:
            return
        self._clicks += 1
        if self._clicks >= self.REQUIRED_CLICKS:
            self._start()

    def _start(self) -> None:
        self._takeover.capture()
        self._takeover.set_axi_interactive(False)
        self._takeover.set_axi_content_visible(False)
        self._prepare_main_takeover()
        self._takeover.show()
        self._takeover.raise_()
        self._takeover.setFocus(Qt.OtherFocusReason)
        self._active = True
        self._set_phase("flicker")
        self._timer.start()

    def _tick(self) -> None:
        elapsed = time.monotonic() - self._phase_started
        duration = self._phase_duration()
        if duration is None:
            self._timer.stop()
            return

        self._takeover.set_progress(elapsed / duration)
        if (
            self._main_takeover is not None
            and self._main_takeover.isVisible()
            and self._main_takeover.phase != "glitch"
        ):
            self._main_takeover.set_progress(self._takeover.progress)
        if elapsed < duration:
            return

        if self._phase == "restore":
            self.reset()
            return

        next_phase = {
            "flicker": "void",
            "void": "hydra",
            "hydra": "after_hydra",
            "after_hydra": "message_one",
            "message_one": "message_two",
            "message_two": "axi_fade",
            "axi_fade": "axi_wait",
        }.get(self._phase)
        if next_phase is not None:
            self._set_phase(next_phase)

    def _phase_duration(self) -> float | None:
        return {
            "flicker": self.FLICKER_SECONDS,
            "void": self.VOID_SECONDS,
            "hydra": self.HYDRA_SECONDS,
            "after_hydra": self.AFTER_HYDRA_SECONDS,
            "message_one": self.MESSAGE_SECONDS,
            "message_two": self.MESSAGE_SECONDS,
            "axi_fade": self.AXI_FADE_SECONDS,
            "restore": self.RESTORE_SECONDS,
        }.get(self._phase)

    def _set_phase(self, phase: str) -> None:
        self._phase = phase
        self._phase_started = time.monotonic()
        self._takeover.set_effect(phase)
        interactive = phase == "axi_wait"
        self._takeover.set_axi_interactive(interactive)
        self._takeover.set_axi_content_visible(interactive)
        self._takeover.update()
        self._sync_main_takeover(phase)
        if interactive:
            self._timer.stop()

    def _ask_humanity(self) -> None:
        if self._phase != "axi_wait" or self._prompt_open:
            return
        self._prompt_open = True
        self._takeover.set_axi_interactive(False)
        try:
            answer = QMessageBox.question(
                self._dialog,
                "Humanity needs you",
                "Do you want to save humanity?",
                QMessageBox.Yes | QMessageBox.No,
                QMessageBox.Yes,
            )
            if answer == QMessageBox.Yes:
                QDesktopServices.openUrl(QUrl(ANTI_XENO_URL))
        finally:
            self._prompt_open = False
            self._begin_restore()

    def _begin_restore(self) -> None:
        if not self._active:
            return
        self._set_phase("restore")
        self._timer.start()

    def reset(self) -> None:
        """Restore the untouched settings panel so the egg can run again."""
        self._timer.stop()
        self._main_glitch_timer.stop()
        self._takeover.clear_effect()
        self._takeover.set_axi_interactive(False)
        self._takeover.set_axi_content_visible(False)
        if self._main_takeover is not None:
            self._main_takeover.clear_effect()
            self._main_takeover.set_axi_content_visible(False)
        self._phase = "idle"
        self._active = False
        self._clicks = 0
        self._panel.update()

    def _prepare_main_takeover(self) -> None:
        if self._main_takeover is None or self._main_window is None:
            return
        self._main_takeover.capture()
        self._main_takeover.set_axi_interactive(False)
        self._main_takeover.set_axi_content_visible(False)

    def _sync_main_takeover(self, phase: str) -> None:
        if self._main_takeover is None or self._main_takeover.snapshot.isNull():
            return

        if phase == "restore":
            self._main_glitch_timer.stop()
            main_phase = "restore"
        elif phase == "flicker":
            main_phase = "flicker"
        else:
            main_phase = "glitch"

        if self._main_takeover.phase != main_phase:
            self._main_takeover.set_effect(main_phase)
        self._main_takeover.show()
        self._main_takeover.raise_()
        self._main_takeover.update()

        if main_phase == "glitch" and not self._main_glitch_timer.isActive():
            self._main_glitch_timer.start()

    def _advance_main_glitch(self) -> None:
        if (
            not self._active
            or self._main_takeover is None
            or self._main_takeover.phase != "glitch"
        ):
            return
        self._main_takeover.advance_glitch(
            self.TICK_MS,
            self.MAIN_GLITCH_CYCLE_MS,
        )
