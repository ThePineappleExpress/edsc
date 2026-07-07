"""Qt engine: owns the app state and pumps journal updates into the GUI thread.
"""

from __future__ import annotations

import threading
from pathlib import Path

from PySide6.QtCore import QObject, QThread, Signal, Slot

from . import core
from .config import Config
from .model import AppState


class _Worker(QObject):
    """Replays journal history and runs the polling loop off the GUI thread."""

    # Bootstrap finished: hands the fully replayed state (and its watcher)
    # over to the GUI thread. Emitted before any live event/cargo signal, so
    # queued delivery order guarantees the GUI owns the state first.
    ready = Signal(object, object)
    failed = Signal(str)
    event = Signal(dict)
    cargo = Signal(list)

    def __init__(self, journal_dir: Path) -> None:
        super().__init__()
        self._journal_dir = journal_dir
        self._stop = threading.Event()

    @Slot()
    def run(self) -> None:
        # Replaying years of journal history can take a while; doing it here
        # keeps the GUI responsive. The state is private to this thread until
        # handed over via ``ready``.
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
        # Live updates cross to the GUI thread as signals instead of mutating
        # state off-thread.
        watcher.on_event = self.event.emit
        watcher.on_cargo = self.cargo.emit
        self.ready.emit(state, watcher)
        watcher.run(should_stop=self._stop.is_set)

    def stop(self) -> None:
        self._stop.set()


class Engine(QObject):
    """Bridges journal reading and the GUI. Emits :attr:`state_changed`."""

    state_changed = Signal()
    status_changed = Signal(str)

    def __init__(self, config: Config, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self.config = config
        self.state = AppState()
        self.watcher = None
        self.journal_dir = None
        self._thread: QThread | None = None
        self._worker: _Worker | None = None
        # Workers whose thread outlived stop()'s grace period; referenced here
        # so Python doesn't destroy them while their thread is still running.
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

        # An explicit override that no longer exists falls back to
        # auto-detection; say so instead of silently watching elsewhere.
        override = (self.config.journal_dir or "").strip()
        self._dir_warning = ""
        if override and Path(override).expanduser() != self.journal_dir:
            self._dir_warning = (
                f"Configured journal folder not found; using {self.journal_dir}. "
            )

        # Show cached data immediately; the (possibly slow) journal replay
        # happens off-thread and swaps the state in when it's done.
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
        self._thread.started.connect(self._worker.run)
        self._thread.start()

    # All slots ignore signals from a superseded worker: after a restart (e.g.
    # journal dir changed in Settings) the old thread may still flush queued
    # signals, and its events must not be applied to the new state.

    @Slot(object, object)
    def _on_ready(self, state: AppState, watcher) -> None:
        if self.sender() is not self._worker:
            return
        self.state = state
        self.watcher = watcher
        self.status_changed.emit(
            self._dir_warning + f"Watching: {self.journal_dir}"
        )
        self.state_changed.emit()

    @Slot(str)
    def _on_failed(self, message: str) -> None:
        if self.sender() is not self._worker:
            return
        self.status_changed.emit(f"Journal replay failed: {message}")

    @Slot(dict)
    def _on_event(self, event: dict) -> None:
        if self.sender() is not self._worker:
            return
        if self.state.apply_event(event):
            self.state_changed.emit()

    @Slot(list)
    def _on_cargo(self, inventory: list) -> None:
        if self.sender() is not self._worker:
            return
        self.state.set_cargo(inventory)
        self.state_changed.emit()

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
                # Still replaying a huge file; let it finish in the background.
                # Its signals are ignored by the sender checks above.
                self._orphans.append((worker, thread))
        try:
            core.save_state(self.state)
        except OSError:
            pass
