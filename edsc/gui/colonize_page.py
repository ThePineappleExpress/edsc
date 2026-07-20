"""The colonisation-target search: nearby unclaimed systems worth settling."""

# SPDX-License-Identifier: GPL-3.0-or-later

from __future__ import annotations

from PySide6.QtCore import QModelIndex, Qt, QThreadPool, QTimer, Signal
from PySide6.QtGui import QFontMetrics, QGuiApplication
from PySide6.QtWidgets import QHeaderView, QLabel, QTableView, QVBoxLayout, QWidget

from .. import systems as system_search
from ..config import Config
from ..model import AppState
from . import icons, theme
from .colonize_filters import ColonizeFilters
from .search_tasks import ColonizeFilterTask, ColonizeSearchTask
from .table_model import (
    SY_AGENT_COL,
    SY_BODIES_COL,
    SY_DIST_COL,
    SY_FURTHEST_COL,
    SY_STARS_COL,
    SY_STEPS_COL,
    SY_SYSTEM_COL,
    SYSTEM_COLUMNS,
    SystemTableModel,
)
from .widgets import ElideLabel, ResultsTable, flash_status

# Coalescing window for a burst of live filter changes (e.g. a slider drag).
_REFILTER_DEBOUNCE_MS = 200


class ColonizePage(QWidget):
    """Nearby unclaimed systems, ranked for colonisation; same once-per-session auto-search contract as the station search -- radius re-queries Spansh, every other control refines the cached pool client-side, off the GUI thread, without another search."""

    # Rows or status text changed -> the shell should re-fit its height.
    content_changed = Signal()

    def __init__(
        self,
        config: Config,
        pool: QThreadPool,
        *,
        task_factory=ColonizeSearchTask,
        filter_task_factory=ColonizeFilterTask,
    ) -> None:
        super().__init__()
        self.config = config
        self._pool = pool
        self._task_factory = task_factory
        self._filter_task_factory = filter_task_factory
        self._state: AppState | None = None
        self.title = "Colonisation targets"
        self.subtitle = ""

        self._seq = 0
        self._searching = False
        self._searched = False
        self._task = None  # holds the in-flight search task reference
        self._ref = ""  # reference system behind the last search
        self._range = 0  # radius (Ly) behind the last search
        # How far that search actually reached; short of the radius when Spansh's row ceiling cut it off. Every status line quotes this, not the radius.
        self._covered_ly: float | None = None
        self._results: list[system_search.SystemResult] = []
        # Every reachable candidate from the last search, unfiltered; the result filters re-slice it client-side without another Spansh search.
        self._pool_results: list[system_search.SystemResult] = []
        self._reachable = 0
        self._filter_seq = 0
        self._filter_task = None
        # Created lazily so headless tests need no event loop.
        self._filter_timer: QTimer | None = None

        root = QVBoxLayout(self)
        root.setContentsMargins(*theme.METRICS.page_margins)
        root.setSpacing(theme.METRICS.content_spacing)

        # The tab is split: results on top, filter deck below; the context line carries the reference system + search scope (its ↻ Search lives in the deck, on the radius row).
        self.ref_label = ElideLabel()
        theme.set_role(self.ref_label, theme.SUBTITLE_ROLE)
        root.addWidget(self.ref_label)

        # Candidate results table: the Agent column takes the flexible slack, the system name is the priority column (and click-to-copy target).
        self.model = SystemTableModel()
        self.table = ResultsTable()
        self.table.flex_col = SY_AGENT_COL
        self.table.setModel(self.model)
        theme.configure_table(
            self.table, elide_text=True, font_pt=self.config.font_point_size
        )
        self.table.setSelectionMode(QTableView.NoSelection)
        self.table.setFocusPolicy(Qt.NoFocus)
        self.table.verticalHeader().setVisible(False)
        self.table.clicked.connect(self._copy_system)
        # All sections Fixed: relayout_columns owns every width.
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.Fixed)
        self.model.modelReset.connect(self.table.relayout_columns)
        root.addWidget(self.table, 1)

        self.status = QLabel("")
        theme.set_role(self.status, theme.STATUS_ROLE)
        self.status.setWordWrap(True)
        root.addWidget(self.status)

        # Filter deck: radius re-queries Spansh (its ↻ Search runs a fresh search), every other control refines the cached pool client-side and fires ``changed`` for a debounced, no-network re-filter.
        self.filters = ColonizeFilters(self.config)
        self.filters.searchRequested.connect(self.refresh)
        self.filters.rangeEdited.connect(self._on_range_edited)
        self.filters.changed.connect(self._schedule_refilter)
        root.addWidget(self.filters)

    #  shell contract

    @property
    def fit_table(self) -> ResultsTable:
        return self.table

    @property
    def busy(self) -> bool:
        return self._searching

    @property
    def pending_task(self):
        """The in-flight search task, or None when idle."""
        return self._task

    @property
    def pending_filter_task(self):
        """The in-flight re-filter task, or None when idle."""
        return self._filter_task

    def header_icon(self):
        return icons.app_glyph_pixmap(32)

    def stop(self) -> None:
        """Abandon every pending and in-flight lookup (called on shutdown); a search or re-filter may still be blocked on the network, and a debounced re-filter may not have started, so none of them may reach widgets about to be destroyed."""
        if self._filter_timer is not None:
            self._filter_timer.stop()
        for task in (self._task, self._filter_task):
            if task is not None:
                task.cancel()

    def apply_appearance(self) -> None:
        theme.update_table_metrics(self.table, self.config.font_point_size)
        self._apply_column_widths()

    def sync_from_config(self) -> None:
        self.filters.sync_from_config()

    def _apply_column_widths(self) -> None:
        """Feed the colonize table its column budgets; same scheme as the station tables -- constant numeric columns from font metrics, then the system name (priority) and the Agent column (flex) share the rest."""
        pt = self.config.font_point_size
        mono_fm = QFontMetrics(theme.monospace_font(pt))
        head_fm = QFontMetrics(theme.resized_font(self.font(), pt - 1))
        pad = theme.scaled_px(theme.METRICS.table_cell_padding, pt)
        header_extra = theme.scaled_px(theme.METRICS.table_header_extra, pt)

        def col_width(col: int, sample: str) -> int:
            header_w = head_fm.horizontalAdvance(SYSTEM_COLUMNS[col]) + header_extra
            return max(mono_fm.horizontalAdvance(sample) + pad, header_w)

        self.table.fixed_widths = {
            SY_DIST_COL: col_width(SY_DIST_COL, "9,999.9"),
            SY_STEPS_COL: col_width(SY_STEPS_COL, "88"),
            SY_STARS_COL: col_width(SY_STARS_COL, "88"),
            SY_BODIES_COL: col_width(SY_BODIES_COL, "888"),
            SY_FURTHEST_COL: col_width(SY_FURTHEST_COL, "9,999.9k"),
        }
        self.table.flex_min = (
            head_fm.horizontalAdvance(SYSTEM_COLUMNS[SY_AGENT_COL]) + 2 * pad
        )
        self.table.priority_min = (
            head_fm.horizontalAdvance(SYSTEM_COLUMNS[SY_SYSTEM_COL]) + 2 * pad
        )
        self.table.relayout_columns()

    #  rendering

    def render(self, state: AppState) -> None:
        """Show the colonize page; the first view triggers the search."""
        self._state = state
        system = state.current_system or "unknown"
        self.subtitle = f"Near {system}"
        range_ly = self.filters.range_ly()
        ref_text = (
            f"Near {system} · within {range_ly} Ly · "
            f"system data ≤{system_search.SYSTEM_DATA_MAX_AGE_DAYS}d old"
        )
        # Results are anchored to where (and how wide) the search ran; still shown afterwards, just flagged so the user knows ↻ re-anchors.
        if self._ref and self._ref != state.current_system:
            ref_text += f" · results from {self._ref} (↻ to update)"
        elif self._range and self._range != range_ly:
            ref_text += " · range changed (↻ to update)"
        self.ref_label.setText(ref_text)

        if not state.current_system:
            self.status.setText(
                "Waiting for your location - jump or dock so EDSC knows where you are."
            )
            self.model.set_rows([])
            return

        # Auto-search only once per session; afterwards Spansh is hit again solely by ↻ Search.
        if not self._searching and not self._searched:
            self._start_search(state)

    def _on_range_edited(self) -> None:
        """The radius slider moved: refresh the ↻ staleness hint, never search."""
        if self._state is not None and self.isVisible():
            self.render(self._state)

    def _copy_system(self, index: QModelIndex) -> None:
        """Clicking a System cell copies the name, ready to paste in-game."""
        if index.column() != SY_SYSTEM_COL:
            return
        result = self.model.row_at(index.row())
        if result is None:
            return
        QGuiApplication.clipboard().setText(result.name)
        flash_status(self.status, f"Copied '{result.name}' to clipboard")

    def _scope(self) -> str:
        """How far the last search reached, phrased for a status line; quotes the distance actually covered, not the radius asked for, since when Spansh's row ceiling truncates a wide search "within 300 Ly" would credit it with systems it never looked at."""
        covered = self._covered_ly
        if covered is not None and covered < self._range - 1:
            return f"the nearest {covered:.0f} Ly"
        return f"{self._range} Ly"

    #  search lifecycle

    def refresh(self) -> None:
        """Manual refresh: force a fresh search."""
        if self._state is not None:
            self._start_search(self._state)

    def _start_search(self, state: AppState) -> None:
        reference = state.current_system
        if not reference:
            return
        if self._filter_timer is not None:
            self._filter_timer.stop()  # a fresh search supersedes it
        self._searching = True
        self._searched = True
        self._seq += 1
        seq = self._seq
        range_ly = self.filters.range_ly()
        self.status.setText(
            f"Searching Spansh for unclaimed systems updated in the last "
            f"{system_search.SYSTEM_DATA_MAX_AGE_DAYS} days within {range_ly} Ly "
            f"of {reference}…"
        )
        self.filters.set_controls_enabled(False)
        task = self._task_factory(
            reference,
            range_ly,
            filters=self.filters.system_filters(),
            sort=self.filters.sort(),
            body_weight=self.config.colonize_body_weight,
        )
        # Keep a Python reference so the task and its signal object aren't GC'd before the queued result reaches the GUI thread.
        task.setAutoDelete(False)
        self._task = task
        task.signals.done.connect(
            lambda search, s=seq, ref=reference, rng=range_ly: (
                self._on_done(s, ref, rng, search)
            )
        )
        task.signals.error.connect(lambda message, s=seq: self._on_error(s, message))
        self._pool.start(task)

    def _on_done(
        self,
        seq: int,
        reference: str,
        range_ly: int,
        search: system_search.ColonizeSearch,
    ) -> None:
        if seq != self._seq:
            return  # a newer search superseded this one
        self._searching = False
        self._task = None
        self.filters.set_controls_enabled(True)
        self._ref = reference
        self._range = range_ly
        self._covered_ly = search.covered_ly
        self._pool_results = list(search.pool)
        self._reachable = search.reachable
        self._results = list(search.results)
        self.model.set_rows(self._results)

        if not self._results:
            filtered = (
                self.filters.system_filters() != system_search.SystemFilters()
            )
            hint = (
                "loosen the filters below and press ↻ Search"
                if filtered
                else "widen the range and press ↻ Search"
            )
            self.status.setText(
                f"No recently updated colonizable systems reachable in "
                f"≤{system_search.MAX_STEPS} steps within {self._scope()}"
                f" - {hint}."
            )
            self.content_changed.emit()
            return
        claimable = sum(1 for r in self._results if r.claimable)
        text = (
            f"Top {len(self._results)} of {search.reachable} reachable "
            f"systems within {self._scope()} · {claimable} claimable now · "
            f"system data ≤{system_search.SYSTEM_DATA_MAX_AGE_DAYS}d old"
        )
        if search.graph_truncated:
            text += (
                f" · Spansh returns at most "
                f"{system_search.GRAPH_ROW_CEILING:,} systems, so nothing beyond "
                f"this was searched"
            )
        if search.ring_error:
            text += " · ring lookup failed (rings not applied)"
        failed = sum(1 for r in self._results if r.agent_error)
        if failed:
            text += f" · {failed} agent lookups failed (↻ to retry)"
        self.status.setText(text)
        self.content_changed.emit()

    def _on_error(self, seq: int, message: str) -> None:
        if seq != self._seq:
            return
        self._searching = False
        self._task = None
        self.filters.set_controls_enabled(True)
        if not self._results:
            self.model.set_rows([])
        self.status.setText(f"Search failed: {message}")

    #  local re-filtering

    def _schedule_refilter(self) -> None:
        """A live filter changed: coalesce a burst into one pool re-filter."""
        if not self._pool_results:
            return  # nothing searched yet; the next ↻ Search applies the filters
        if self._filter_timer is None:
            self._filter_timer = QTimer(self)
            self._filter_timer.setSingleShot(True)
            self._filter_timer.setInterval(_REFILTER_DEBOUNCE_MS)
            self._filter_timer.timeout.connect(self._apply_refilter)
        self._filter_timer.start()

    def _apply_refilter(self) -> None:
        """Re-slice the cached pool for the current filters, off the GUI thread."""
        if self._searching or not self._pool_results:
            return  # a full search is running / will render with these filters
        self._filter_seq += 1
        seq = self._filter_seq
        task = self._filter_task_factory(
            list(self._pool_results),
            self.filters.system_filters(),
            sort=self.filters.sort(),
            body_weight=self.config.colonize_body_weight,
        )
        task.setAutoDelete(False)
        self._filter_task = task
        task.signals.done.connect(
            lambda result, s=seq: self._on_filtered(s, result)
        )
        task.signals.error.connect(
            lambda message, s=seq: self._on_filter_error(s, message)
        )
        self._pool.start(task)

    def _on_filtered(self, seq: int, result: system_search.FilterResult) -> None:
        if seq != self._filter_seq:
            return  # a newer re-filter superseded this one
        self._filter_task = None
        self._results = list(result.results)
        self.model.set_rows(self._results)
        self._set_filtered_status(result)
        self.content_changed.emit()

    def _on_filter_error(self, seq: int, message: str) -> None:
        if seq != self._filter_seq:
            return
        self._filter_task = None
        self.status.setText(f"Filter failed: {message}")

    def _set_filtered_status(self, result: system_search.FilterResult) -> None:
        shown = len(self._results)
        if shown == 0:
            self.status.setText(
                f"None of {self._reachable} reachable systems within "
                f"{self._scope()} match these filters - loosen them "
                f"below, or press ↻ Search to rescan."
            )
            return
        claimable = sum(1 for r in self._results if r.claimable)
        text = (
            f"{shown} of {result.matched} matching · {self._reachable} "
            f"reachable within {self._scope()} · {claimable} claimable now"
        )
        if result.ring_error:
            text += " · ring lookup failed (rings not applied)"
        self.status.setText(text)
