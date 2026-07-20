"""Cancellable background tasks for the overlay's network searches."""

# SPDX-License-Identifier: GPL-3.0-or-later

from __future__ import annotations

from collections.abc import Callable
from contextlib import suppress
from functools import partial

from PySide6.QtCore import QObject, QRunnable, Signal

from .. import stations, systems


class SearchSignals(QObject):
    done = Signal(object)
    error = Signal(str)


class BackgroundSearchTask(QRunnable):
    """Run one search and publish its result unless the task was cancelled."""

    def __init__(self, search: Callable[[], object]) -> None:
        super().__init__()
        self.signals = SearchSignals()
        self._search = search
        self._cancelled = False

    def cancel(self) -> None:
        self._cancelled = True

    def _emit(self, name: str, payload: object) -> None:
        if self._cancelled:
            return
        with suppress(RuntimeError):
            getattr(self.signals, name).emit(payload)

    def run(self) -> None:
        try:
            result = self._search()
        except Exception as exc:  # network boundary: surface failures in the UI
            self._emit("error", str(exc))
        else:
            self._emit("done", result)


class StationSearchTask(BackgroundSearchTask):
    def __init__(
        self,
        reference_system: str,
        needed: dict[str, int],
        recent_only: bool,
        range_ly: int = 0,
        sort: str = "match",
    ) -> None:
        super().__init__(
            partial(
                stations.search_stations,
                reference_system,
                needed,
                recent_only=recent_only,
                range_ly=range_ly,
                sort=sort,
            )
        )


class ColonizeSearchTask(BackgroundSearchTask):
    def __init__(
        self,
        reference_system: str,
        range_ly: int,
        filters: systems.SystemFilters | None = None,
        sort: str = "balanced",
        body_weight: float = 1.0,
    ) -> None:
        super().__init__(
            partial(
                systems.search_colonisation_targets,
                reference_system,
                range_ly,
                filters=filters,
                sort=sort,
                body_weight=body_weight,
            )
        )


class ColonizeFilterTask(BackgroundSearchTask):
    """Re-slice a cached candidate pool for a filter change (no fresh search); free filters run in memory and ring/verification/agent lookups for the resulting page fill from session caches, so this is near-instant unless new systems surface."""

    def __init__(
        self,
        pool: list[systems.SystemResult],
        filters: systems.SystemFilters,
        sort: str = "balanced",
        body_weight: float = 1.0,
    ) -> None:
        super().__init__(
            partial(
                systems.refilter_colonisation,
                pool,
                filters,
                sort=sort,
                body_weight=body_weight,
            )
        )
