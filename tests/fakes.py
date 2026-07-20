"""Test doubles for the overlay's background search tasks; pages take their task class as a ctor arg, so a test hands them ``FakeSearches()`` and drives a search by hand -- no network/threads/waiting."""

# SPDX-License-Identifier: GPL-3.0-or-later

from __future__ import annotations

from PySide6.QtCore import QRunnable

from edsc.gui.search_tasks import SearchSignals


class FakeTask(QRunnable):
    """A search that does nothing until the test says how it ended."""

    def __init__(self, args: tuple, kwargs: dict) -> None:
        super().__init__()
        self.args = args
        self.kwargs = kwargs
        self.signals = SearchSignals()
        self.cancelled = False

    def run(self) -> None:
        """Never fetches: the test publishes the outcome instead."""

    def cancel(self) -> None:
        self.cancelled = True

    def done(self, payload) -> None:
        """Publish a successful result, as the real task would."""
        self.signals.done.emit(payload)

    def fail(self, message: str) -> None:
        """Publish a failure, as the real task would."""
        self.signals.error.emit(message)


class FakeSearches:
    """A task factory that records every search a page starts."""

    def __init__(self) -> None:
        self.tasks: list[FakeTask] = []

    def __call__(self, *args, **kwargs) -> FakeTask:
        task = FakeTask(args, kwargs)
        self.tasks.append(task)
        return task

    @property
    def count(self) -> int:
        return len(self.tasks)

    @property
    def last(self) -> FakeTask:
        assert self.tasks, "expected a search to have been started"
        return self.tasks[-1]
