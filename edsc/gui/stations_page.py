"""The nearest-stations search: where to buy what a construction still needs."""

# SPDX-License-Identifier: GPL-3.0-or-later

from __future__ import annotations

from PySide6.QtCore import QAbstractTableModel, QModelIndex, Qt, QThreadPool, Signal
from PySide6.QtGui import QFontMetrics, QGuiApplication
from PySide6.QtWidgets import (
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QTableView,
    QVBoxLayout,
    QWidget,
)

from .. import stations as station_search
from ..config import Config
from ..model import AppState
from . import icons, theme
from .search_tasks import StationSearchTask
from .table_model import (
    ST_ARRIVAL_COL,
    ST_COVER_COL,
    ST_DIST_COL,
    ST_MATCH_COL,
    ST_SYSTEM_COL,
    STATION_COLUMNS,
    StationTableModel,
)
from .widgets import ElideLabel, ResultsTable, flash_status, tool_button

# Each visible station table is capped to ten mixed-category entries; the cache still keeps ten orbitals, ten planetary, and ten carriers for instant re-filtering and completeness checks.
_RESULTS_SHOWN = 10


class StationsPage(QWidget):
    """Nearest markets selling the outstanding commodities; Spansh is queried automatically only once per session (first view with a known location and outstanding needs), after which only ↻ Search or the Recent toggle re-runs it, and every station-type control filters the cached pool locally without I/O."""

    # Rows or status text changed -> the shell should re-fit its height.
    content_changed = Signal()

    def __init__(
        self,
        config: Config,
        pool: QThreadPool,
        *,
        task_factory=StationSearchTask,
    ) -> None:
        super().__init__()
        self.config = config
        self._pool = pool
        self._task_factory = task_factory
        self._state: AppState | None = None
        self.title = "Nearest stations"
        self.subtitle = ""

        self._seq = 0
        self._searching = False
        self._searched = False
        self._task = None  # holds the in-flight task reference
        self._ref = ""  # reference system behind the last search
        self._needs: dict[str, int] = {}
        self._recent_only = False
        self._results: list[station_search.StationResult] = []

        root = QVBoxLayout(self)
        root.setContentsMargins(*theme.METRICS.page_margins)
        root.setSpacing(theme.METRICS.content_spacing)

        # Controls: reference system + refresh; the search is always large-pad only, so there's no pad filter, and the label elides so its long text never widens the window's minimum.
        bar = QHBoxLayout()
        self.ref_label = ElideLabel()
        theme.set_role(self.ref_label, theme.SUBTITLE_ROLE)
        bar.addWidget(self.ref_label, 1)
        self.planets_btn = tool_button(
            "Planets",
            "Add planetary outposts to the orbital-station results",
            checkable=True,
        )
        self.planets_btn.setChecked(self.config.stations_include_planets)
        self.planets_btn.toggled.connect(self._toggle_include_planets)
        bar.addWidget(self.planets_btn)
        self.carriers_btn = tool_button(
            "Carriers",
            "Add fleet carriers to the orbital-station results",
            checkable=True,
        )
        self.carriers_btn.setChecked(self.config.stations_include_carriers)
        self.carriers_btn.toggled.connect(self._toggle_include_carriers)
        bar.addWidget(self.carriers_btn)
        self.recent_btn = tool_button(
            "Recent",
            "Search only markets updated in the last 24 hours (runs a new search)",
            checkable=True,
        )
        self.recent_btn.setChecked(self.config.stations_recent_only)
        self.recent_btn.toggled.connect(self._toggle_recent)
        bar.addWidget(self.recent_btn)
        self.refresh_btn = tool_button(
            "↻ Search", "Search Spansh for nearby stations"
        )
        self.refresh_btn.clicked.connect(self.refresh)
        bar.addWidget(self.refresh_btn)
        root.addLayout(bar)

        # Station results table.
        self.model = StationTableModel()
        self.table = ResultsTable()
        self._init_table(self.table, self.model)
        root.addWidget(self.table, 1)

        # Secondary table: stations covering the residual demand the best station can't fully supply; its independent filters apply to the same cached pool, so broadening it is instant and does no I/O.
        self.more_bar = QWidget()
        more_bar = QHBoxLayout(self.more_bar)
        more_bar.setContentsMargins(*theme.METRICS.page_margins)
        self.more_label = QLabel("")
        theme.set_role(self.more_label, theme.SUBTITLE_ROLE)
        self.more_label.setWordWrap(True)
        more_bar.addWidget(self.more_label, 1)
        self.planets_btn2 = tool_button(
            "Planets",
            "Add planetary outposts to the orbital remaining-demand results",
            checkable=True,
        )
        self.planets_btn2.setChecked(self.config.stations_include_planets)
        self.planets_btn2.toggled.connect(self._on_follow_up_filter_changed)
        more_bar.addWidget(self.planets_btn2)
        self.carriers_btn2 = tool_button(
            "Carriers",
            "Add fleet carriers to the orbital remaining-demand results",
            checkable=True,
        )
        self.carriers_btn2.setChecked(self.config.stations_include_carriers)
        self.carriers_btn2.toggled.connect(self._on_follow_up_filter_changed)
        more_bar.addWidget(self.carriers_btn2)
        self.more_bar.setVisible(False)
        root.addWidget(self.more_bar)

        self.model2 = StationTableModel()
        self.table2 = ResultsTable()
        self._init_table(self.table2, self.model2)
        self.table2.setVisible(False)
        root.addWidget(self.table2, 1)

        self.status = QLabel("")
        theme.set_role(self.status, theme.STATUS_ROLE)
        self.status.setWordWrap(True)
        root.addWidget(self.status)

    def _init_table(self, table: ResultsTable, model: QAbstractTableModel) -> None:
        table.setModel(model)
        theme.configure_table(
            table, elide_text=True, font_pt=self.config.font_point_size
        )
        table.setSelectionMode(QTableView.NoSelection)
        table.setFocusPolicy(Qt.NoFocus)
        table.verticalHeader().setVisible(False)
        table.clicked.connect(self._copy_system)
        # All sections Fixed: relayout_columns owns every width (constant numerics, then the priority name first, the flex column with the leftovers).
        table.horizontalHeader().setSectionResizeMode(QHeaderView.Fixed)
        model.modelReset.connect(table.relayout_columns)

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

    def header_icon(self):
        return icons.app_glyph_pixmap(32)

    def stop(self) -> None:
        # A search may still be blocked on the network; make sure it won't emit into widgets that are about to be destroyed.
        if self._task is not None:
            self._task.cancel()

    def apply_appearance(self) -> None:
        for table in (self.table, self.table2):
            theme.update_table_metrics(table, self.config.font_point_size)
        self._apply_column_widths()

    def sync_from_config(self) -> None:
        """Mirror the toggles the Settings dialog can also change; signals are blocked so setting them here never kicks off a search (the current values are already live in ``self.config``)."""
        for button, value in (
            (self.planets_btn, self.config.stations_include_planets),
            (self.planets_btn2, self.config.stations_include_planets),
            (self.carriers_btn, self.config.stations_include_carriers),
            (self.carriers_btn2, self.config.stations_include_carriers),
            (self.recent_btn, self.config.stations_recent_only),
        ):
            button.blockSignals(True)
            button.setChecked(value)
            button.blockSignals(False)

    def _apply_column_widths(self) -> None:
        """Feed the station tables their column budgets; numeric columns get constant widths from font metrics (so the font-size setting scales them) sized to the widest realistic value (numbers must never elide), while the system column starts at a small elided floor and relayout_columns grows it only with space the station name doesn't need."""
        pt = self.config.font_point_size
        mono_fm = QFontMetrics(theme.monospace_font(pt))
        head_fm = QFontMetrics(theme.resized_font(self.font(), pt - 1))
        pad = theme.scaled_px(theme.METRICS.table_cell_padding, pt)
        header_extra = theme.scaled_px(theme.METRICS.table_header_extra, pt)

        def col_width(col: int, sample: str, fm: QFontMetrics) -> int:
            header_w = head_fm.horizontalAdvance(STATION_COLUMNS[col]) + header_extra
            return max(fm.horizontalAdvance(sample) + pad, header_w)

        widths = {
            ST_MATCH_COL: col_width(ST_MATCH_COL, "88/88", mono_fm),
            ST_COVER_COL: col_width(ST_COVER_COL, "100%", mono_fm),
            ST_DIST_COL: col_width(ST_DIST_COL, "9,999.9", mono_fm),
            ST_ARRIVAL_COL: col_width(ST_ARRIVAL_COL, "9,999.9k", mono_fm),
        }
        # Floors the shared name columns collapse to: the header word plus a breath of space each side, so the label never clips; System yields to this floor first, the station name follows once system is there.
        system_min = head_fm.horizontalAdvance("System") + 2 * pad
        station_min = head_fm.horizontalAdvance("Station") + 2 * pad
        for table in (self.table, self.table2):
            table.fixed_widths = widths
            table.flex_min = system_min
            table.priority_min = station_min
            table.relayout_columns()

    #  rendering

    def render(self, state: AppState) -> None:
        """Show the search page; the first view triggers the search."""
        self._state = state
        system = state.current_system or "unknown"
        self.subtitle = f"Buying near {system}"
        needs = state.outstanding_needs()
        ref_text = f"Near {system} · {len(needs)} commodities needed"
        if self.recent_btn.isChecked():
            ref_text += " · markets ≤24h old"
        # Results are anchored to where the search ran; after a jump they're still shown, just flagged so the user knows ↻ re-anchors them.
        if self._ref and self._ref != state.current_system:
            ref_text += f" · results from {self._ref} (↻ to update)"
        elif self._needs and self._needs != needs:
            ref_text += " · demand changed (↻ to update)"
        self.ref_label.setText(ref_text)

        if not state.current_system:
            self.status.setText(
                "Waiting for your location - jump or dock so EDSC knows where you are."
            )
            self.model.set_rows([])
            self._clear_follow_up()
            return
        if not needs:
            self.status.setText("Nothing outstanding - all commodities covered.")
            self.model.set_rows([])
            self._clear_follow_up()
            return

        # Auto-search only once per session; afterwards Spansh is hit again solely by ↻ Search or the Recent pre-search toggle.
        if not self._searching and not self._searched:
            self._start_search(state)
        elif not self._searching and self._results:
            self._apply_cached_results()

    def _clear_follow_up(self) -> None:
        """Empty and hide the complementary 'fill the rest at' table."""
        self.model2.set_rows([])
        self.more_bar.setVisible(False)
        self.table2.setVisible(False)

    #  controls

    def refresh(self) -> None:
        """Manual refresh: force a fresh search."""
        if self._state is not None:
            self._start_search(self._state)

    def _toggle_include_planets(self, checked: bool) -> None:
        """Apply the primary surface filter to the cached result pool."""
        self.config.stations_include_planets = checked
        self._apply_cached_results()

    def _toggle_include_carriers(self, checked: bool) -> None:
        """Apply the primary carrier filter to the cached result pool."""
        self.config.stations_include_carriers = checked
        self._apply_cached_results()

    def _toggle_recent(self, checked: bool) -> None:
        """Change the API freshness pre-filter and immediately run it."""
        self.config.stations_recent_only = checked
        self.refresh()

    def _on_follow_up_filter_changed(self, _checked: bool) -> None:
        """Apply a supplementary filter to the cached candidates; these deliberately don't touch the config, so the remaining-demand table broadens independently of the primary results."""
        self._apply_cached_results()

    def _set_controls_enabled(self, enabled: bool) -> None:
        for button in (
            self.planets_btn,
            self.carriers_btn,
            self.recent_btn,
            self.refresh_btn,
            self.planets_btn2,
            self.carriers_btn2,
        ):
            button.setEnabled(enabled)

    def _copy_system(self, index: QModelIndex) -> None:
        """Clicking a System cell copies the name, ready to paste in-game."""
        if index.column() != ST_SYSTEM_COL:
            return
        table = self.sender()
        model = table.model() if table is not None else self.model
        result = model.row_at(index.row())
        if result is None:
            return
        QGuiApplication.clipboard().setText(result.system)
        flash_status(self.status, f"Copied '{result.system}' to clipboard")

    #  search lifecycle

    def _start_search(self, state: AppState) -> None:
        reference = state.current_system
        needs = dict(state.outstanding_needs())
        if not reference or not needs:
            return
        self._searching = True
        self._searched = True
        self._seq += 1
        seq = self._seq
        recent_only = self.recent_btn.isChecked()
        qualifier = " recent" if recent_only else ""
        self.status.setText(
            f"Searching Spansh near {reference} for 10{qualifier} orbital, "
            f"10 planetary, and 10 carrier markets…"
        )
        self._set_controls_enabled(False)
        # Pass the amounts too: a station counts as stocking a commodity only when its supply covers (a useful chunk of) the shortfall.
        task = self._task_factory(
            reference,
            needs,
            recent_only,
            range_ly=self.config.stations_range_ly,
            sort=self.config.stations_sort,
        )
        # Keep a Python reference so the task and its signal object aren't GC'd before the queued result reaches the GUI thread.
        task.setAutoDelete(False)
        self._task = task
        task.signals.done.connect(
            lambda results, s=seq, ref=reference, demand=needs, recent=recent_only: (
                self._on_done(s, ref, demand, recent, results)
            )
        )
        task.signals.error.connect(
            lambda message, s=seq: self._on_error(s, message)
        )
        self._pool.start(task)

    def _on_done(
        self,
        seq: int,
        reference: str,
        needed: dict[str, int],
        recent_only: bool,
        results: list,
    ) -> None:
        if seq != self._seq:
            return  # a newer search superseded this one
        self._searching = False
        self._task = None
        self._set_controls_enabled(True)
        self._ref = reference
        self._needs = dict(needed)
        self._recent_only = recent_only
        self._results = list(results)

        if not self._results:
            self.model.set_rows([])
            self._clear_follow_up()
            self.status.setText("No stations found selling those commodities nearby.")
            self.content_changed.emit()
            return
        self._apply_cached_results()

    def _on_error(self, seq: int, message: str) -> None:
        if seq != self._seq:
            return
        self._searching = False
        self._task = None
        self._set_controls_enabled(True)
        if not self._results:
            self.model.set_rows([])
            self._clear_follow_up()
        self.status.setText(f"Search failed: {message}")

    #  local re-filtering

    def _apply_cached_results(self) -> None:
        """Rebuild both station tables from the one fetched pool, without I/O."""
        if not self._results or not self._needs:
            return
        primary_results = station_search.filter_stations(
            self._results,
            include_planetary=self.config.stations_include_planets,
            include_carriers=self.config.stations_include_carriers,
        )
        self.model.set_rows(
            station_search.limit_mixed_results(primary_results, _RESULTS_SHOWN)
        )
        primary = self.model.row_at(0)
        if primary is None:
            self._clear_follow_up()
            self.status.setText(
                "No cached stations match the primary filters - enable Planets "
                "or Carriers, or press ↻ Search."
            )
            self.content_changed.emit()
            return

        residual = station_search.residual_demand(self._needs, primary)
        secondary_pool = station_search.filter_stations(
            self._results,
            include_planetary=self.planets_btn2.isChecked(),
            include_carriers=self.carriers_btn2.isChecked(),
        )
        follow_up = station_search.limit_mixed_results(
            station_search.supplementary_candidates(
                secondary_pool, self._needs, primary
            ),
            _RESULTS_SHOWN,
        )
        self._show_follow_up(follow_up, residual)

        # Completeness uses a greedy stop plan independent of the alternatives shown above: one planetary station completing the plan must not suppress useful carrier alternatives in the table.
        filtered_plan = station_search.supplementary_stations(
            secondary_pool, self._needs, primary, limit=_RESULTS_SHOWN
        )
        filtered_remaining = station_search.remaining_demand(
            self._needs, [primary, *filtered_plan]
        )
        complete_follow_up = station_search.supplementary_stations(
            self._results, self._needs, primary, limit=_RESULTS_SHOWN
        )
        pool_remaining = station_search.remaining_demand(
            self._needs, [primary, *complete_follow_up]
        )
        self.status.setText(
            self._status_text(primary, residual, filtered_plan, filtered_remaining,
                              pool_remaining)
        )
        self.content_changed.emit()

    def _status_text(
        self,
        primary,
        residual: dict,
        filtered_plan: list,
        filtered_remaining: dict,
        pool_remaining: dict,
    ) -> str:
        orbital, planetary, carriers = self._category_counts(self._results)
        text = (
            f"Cached {orbital} orbital · {planetary} planetary · {carriers} carriers"
            f" · best {primary.match_count}/{primary.needed_total} "
            f"({primary.coverage * 100:.0f}%) at {primary.distance_ly:,.1f} ly"
        )
        if pool_remaining:
            text += (
                f" · {self._shortfall_text(pool_remaining)} not found in fetched data"
            )
        elif filtered_remaining:
            text += (
                f" · {self._shortfall_text(filtered_remaining)} hidden by the "
                "supplementary filters"
            )
        elif residual:
            text += f" · complete in {1 + len(filtered_plan)} stops"
        else:
            text += " · complete in one stop"
        if self._recent_only:
            text += " · ≤24h old"
        return text

    @staticmethod
    def _category_counts(results: list) -> tuple[int, int, int]:
        categories = [station_search.station_category(s) for s in results]
        return tuple(
            categories.count(kind) for kind in ("orbital", "planetary", "carrier")
        )

    @staticmethod
    def _shortfall_text(remaining: dict[str, int]) -> str:
        names = sorted(remaining)
        text = ", ".join(names[:3])
        if len(names) > 3:
            text += f" +{len(names) - 3} more"
        return text

    def _show_follow_up(self, results: list, residual: dict) -> None:
        """Display a locally planned supplementary stop list; the section stays visible after an empty result so its independent filters remain available for broadening the cached candidates."""
        self.model2.set_rows(results[:_RESULTS_SHOWN])
        has_residual = bool(residual)
        self.more_label.setText(
            "Fill the rest at:"
            if results
            else "No cached stations match the remaining demand:"
        )
        self.more_bar.setVisible(has_residual)
        self.table2.setVisible(has_residual)
