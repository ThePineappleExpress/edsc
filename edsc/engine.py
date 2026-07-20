"""Pump journal updates from a worker into the Qt GUI thread."""

# SPDX-License-Identifier: GPL-3.0-or-later

from __future__ import annotations

import threading
from contextlib import suppress
from pathlib import Path

from PySide6.QtCore import QObject, QThread, Signal, Slot

from . import core
from .config import Config
from .model import AppState


class _Worker(QObject):
    """Replays journal history and runs the polling loop off the GUI thread."""

    # Bootstrap finished: hands the fully replayed state (and watcher) to the GUI thread, before any live signal, so queued delivery order guarantees the GUI owns the state first.
    ready = Signal(object, object)
    failed = Signal(str)
    # ``object``, not ``dict``/``list``: a typed queued signal converts to QVariantMap across the thread boundary, and Elite's unsigned 64-bit ids (up to 2**64-1) overflow Qt's signed long long and drop the whole event; ``object`` passes the Python value by reference, untouched.
    event = Signal(object)
    cargo = Signal(object)
    market = Signal(object)

    def __init__(self, journal_dir: Path) -> None:
        super().__init__()
        self._journal_dir = journal_dir
        self._stop = threading.Event()

    @Slot()
    def run(self) -> None:
        # Replaying years of journal history can be slow; here it keeps the GUI responsive, state private to this thread until handed over via ``ready``.
        try:
            state = core.load_cached_state()
            state, watcher = core.bootstrap(
                self._journal_dir, state, should_stop=self._stop.is_set
            )
        except Exception as exc:
            self.failed.emit(str(exc))
            return
        if self._stop.is_set():
            return
        # Live updates cross to the GUI thread as signals instead of mutating state off-thread.
        watcher.on_event = self.event.emit
        watcher.on_cargo = self.cargo.emit
        watcher.on_market = self.market.emit
        self.ready.emit(state, watcher)
        watcher.run(should_stop=self._stop.is_set)

    def stop(self) -> None:
        self._stop.set()


class Engine(QObject):
    """Bridges journal reading and the GUI. Emits :attr:`state_changed`."""

    state_changed = Signal()
    status_changed = Signal(str)
    # Public live-only stream: replay completes before callbacks switch to signals, so consumers (e.g. encounter effects) never see historical entries on startup; ``object`` (not ``dict``) so big unsigned ids survive a cross-thread consumer (see worker signals above).
    live_event = Signal(object)
    ready = Signal()

    def __init__(self, config: Config, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self.config = config
        self.state = AppState()
        self.watcher = None
        self.journal_dir = None
        # Optional EDDN relay, owned by the Application and set only while the user consents; sees live (post-replay) events, armed on ready; None means sharing is off.
        self.uplink = None
        self._thread: QThread | None = None
        self._worker: _Worker | None = None
        # Workers whose thread outlived stop()'s grace period, kept referenced so Python doesn't destroy them mid-run.
        self._orphans: list[tuple[_Worker, QThread]] = []
        self._dir_warning = ""

    def start(self) -> None:
        self.journal_dir = core.resolve_journal_dir(self.config)
        if self.journal_dir is None:
            self.status_changed.emit(
                "No Elite Dangerous journal folder found. Set one in Settings."
            )
            self.state_changed.emit()
            return

        # An explicit override that no longer exists falls back to auto-detection; say so instead of silently watching elsewhere.
        override = (self.config.journal_dir or "").strip()
        self._dir_warning = ""
        if override and Path(override).expanduser() != self.journal_dir:
            self._dir_warning = (
                f"Configured journal folder not found; using {self.journal_dir}. "
            )

        # Show cached data immediately; the (possibly slow) journal replay runs off-thread and swaps state in when done.
        self.state = core.load_cached_state()
        self.state_changed.emit()
        self.status_changed.emit(self._dir_warning + "Replaying journal history…")

        self._thread = QThread(self)
        self._worker = _Worker(self.journal_dir)
        self._worker.moveToThread(self._thread)
        self._worker.ready.connect(self._on_ready)
        self._worker.failed.connect(self._on_failed)
        self._worker.event.connect(self._on_event)
        self._worker.cargo.connect(self._on_cargo)
        self._worker.market.connect(self._on_market)
        self._thread.started.connect(self._worker.run)
        self._thread.start()

    # All slots ignore signals from a superseded worker: after a restart (e.g. journal dir change) the old thread may still flush queued signals that must not touch the new state.

    @Slot(object, object)
    def _on_ready(self, state: AppState, watcher) -> None:
        if self.sender() is not self._worker:
            return
        self.state = state
        self.watcher = watcher
        # History is fully replayed now; allow the relay to submit the live events that follow (the builders' freshness gate backstops this).
        if self.uplink is not None:
            self.uplink.arm()
        self.status_changed.emit(
            self._dir_warning + f"Watching: {self.journal_dir}"
        )
        self.ready.emit()
        self.state_changed.emit()

    @Slot(str)
    def _on_failed(self, message: str) -> None:
        if self.sender() is not self._worker:
            return
        self.status_changed.emit(f"Journal replay failed: {message}")

    @Slot(object)
    def _on_event(self, event: dict) -> None:
        if self.sender() is not self._worker:
            return
        # The relay tracks session/location from every event, even ones that don't change AppState, so submit augmentation stays correct.
        if self.uplink is not None:
            self.uplink.handle_event(event)
        if self.state.apply_event(event):
            self.state_changed.emit()
        self.live_event.emit(event)

    @Slot(object)
    def _on_cargo(self, inventory: list) -> None:
        if self.sender() is not self._worker:
            return
        self.state.set_cargo(inventory)
        self.state_changed.emit()

    @Slot(object)
    def _on_market(self, data: dict) -> None:
        if self.sender() is not self._worker:
            return
        if self.uplink is not None:
            self.uplink.handle_market(data)
        self.state.set_market(data)
        self.state_changed.emit()

    def sync_eddn_now(self) -> tuple[bool, bool] | None:
        """Force the relay to share the current session on user request; re-reads the newest journal and Market.json so the push reflects disk now (not just live events since arming), on the GUI thread (no race). Returns ``(journal_sent, market_sent)`` or None when sharing is off / no dir."""
        if self.uplink is None or self.journal_dir is None:
            return None
        events = core.read_session_events(self.journal_dir)
        market = core.read_market_snapshot(self.journal_dir)
        return self.uplink.sync_now(events, market)

    def stop(self) -> None:
        """Stop the worker thread and persist state."""
        worker, thread = self._worker, self._thread
        self._worker = None
        self._thread = None
        if worker is not None:
            worker.stop()
        if thread is not None:
            thread.quit()
            if not thread.wait(2000) and worker is not None:
                # Still replaying a huge file; let it finish in the background (its signals are ignored by the sender checks above).
                self._orphans.append((worker, thread))
        with suppress(OSError):
            core.save_state(self.state)
