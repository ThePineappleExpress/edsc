"""Application-wide visual response to live Thargoid journal evidence."""

# SPDX-License-Identifier: GPL-3.0-or-later

from __future__ import annotations

import time

from PySide6.QtCore import QObject, Qt, QTimer, Signal, Slot
from PySide6.QtWidgets import QWidget

from ..thargoids import ThargoidDetector, ThargoidEvidence
from .glitch_overlay import DEFAULT_GLITCH_CYCLE_MS, GlitchOverlay


class ThargoidEffectController(QObject):
    """Drive reusable glitch canvases from a live :class:`ThargoidDetector`."""

    TICK_MS = 33
    GLITCH_CYCLE_MS = DEFAULT_GLITCH_CYCLE_MS
    # Real journals provide SystemsShutdown but no matching systems-restored event; a shutdown field lasts ~30 seconds, so recover on a calibrated fallback unless a definitive encounter-ending event arrives first.
    BLACKOUT_SECONDS = 30.0

    evidence_detected = Signal(object)
    encounter_ended = Signal()

    def __init__(self, target: QWidget, parent: QObject | None = None) -> None:
        super().__init__(parent or target)
        self.detector = ThargoidDetector()
        self._effects: list[GlitchOverlay] = []
        self._phase = "idle"
        self._blackout_until = 0.0
        self._timer = QTimer(self)
        self._timer.setInterval(self.TICK_MS)
        self._timer.timeout.connect(self._tick)
        self.add_target(target)

    @property
    def phase(self) -> str:
        return self._phase

    @property
    def effects(self) -> tuple[GlitchOverlay, ...]:
        return tuple(self._effects)

    def add_target(self, target: QWidget) -> GlitchOverlay:
        """Cover another app-owned window with the current encounter effect."""
        effect = GlitchOverlay(target)
        effect.setAttribute(Qt.WA_TransparentForMouseEvents)
        self._effects.append(effect)
        if self._phase != "idle":
            effect.start_effect(self._phase)
        return effect

    def remove_target(self, target: QWidget | GlitchOverlay) -> None:
        """Detach and destroy the effect canvas belonging to ``target``."""
        for effect in tuple(self._effects):
            if effect is target or effect.parentWidget() is target:
                self._effects.remove(effect)
                effect.clear_effect()
                effect.deleteLater()

    def reset(self, current_system: str = "") -> None:
        """Clear detection and visuals, optionally seeding the known system."""
        self.detector.reset(current_system)
        self._clear_visuals()

    @Slot(dict)
    def handle_event(self, event: dict) -> None:
        """Consume one raw live journal event on the GUI thread."""
        evidence = self.detector.process(event)
        if evidence is None:
            return

        if evidence is ThargoidEvidence.ENCOUNTER_ENDED:
            self._clear_visuals()
            self.encounter_ended.emit()
            return

        self.evidence_detected.emit(evidence)
        if evidence is ThargoidEvidence.SYSTEMS_SHUTDOWN:
            self._start_blackout()
        else:
            self._start_glitch()

    def _start_glitch(self) -> None:
        if self._phase == "blackout":
            return
        self._show_phase("glitch")
        if not self._timer.isActive():
            self._timer.start()

    def _start_blackout(self) -> None:
        self._blackout_until = time.monotonic() + self.BLACKOUT_SECONDS
        self._show_phase("blackout")
        if not self._timer.isActive():
            self._timer.start()

    def _show_phase(self, phase: str) -> None:
        self._phase = phase
        for effect in self._effects:
            if effect.phase == "idle" or effect.snapshot.isNull():
                effect.start_effect(phase)
            else:
                effect.set_effect(phase)

    def _clear_visuals(self) -> None:
        self._phase = "idle"
        self._blackout_until = 0.0
        self._timer.stop()
        for effect in self._effects:
            effect.clear_effect()

    def _tick(self) -> None:
        if self._phase == "blackout":
            if time.monotonic() < self._blackout_until:
                return
            self._blackout_until = 0.0
            if self.detector.encounter_active:
                self._show_phase("glitch")
            else:
                self._clear_visuals()
            return

        if self._phase != "glitch":
            self._timer.stop()
            return
        for effect in self._effects:
            effect.advance_glitch(self.TICK_MS, self.GLITCH_CYCLE_MS)
