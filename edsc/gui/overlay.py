"""The in-game overlay window.


    EDSC - Colonization commodities tracker
    Copyright (C) 2026  ThePineappleExpress

    This program is free software: you can redistribute it and/or modify
    it under the terms of the GNU General Public License as published by
    the Free Software Foundation, either version 3 of the License, or
    (at your option) any later version.

    This program is distributed in the hope that it will be useful,
    but WITHOUT ANY WARRANTY; without even the implied warranty of
    MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
    GNU General Public License for more details.

    You should have received a copy of the GNU General Public License
    along with this program.  If not, see <https://www.gnu.org/licenses/>.

"""

from __future__ import annotations

from PySide6.QtCore import (
    QEvent,
    QObject,
    QPoint,
    QRunnable,
    QSize,
    Qt,
    QThreadPool,
    QTimer,
    Signal,
)
from PySide6.QtGui import QKeySequence, QMouseEvent, QShortcut
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QMenu,
    QProgressBar,
    QSizeGrip,
    QSizePolicy,
    QTableView,
    QTabBar,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from ..config import Config
from ..model import COMBINED_MARKET_ID, STATIONS_MARKET_ID, AppState, CommodityRow, Project
from ..platform import clickthrough, foreground, topmost
from .. import __version__
from .. import stations as station_search
from . import theme
from .carrier_dialog import CarrierCargoDialog
from .table_model import CommodityTableModel, StationTableModel

# How often to check which window is focused (ms).
_FOCUS_POLL_MS = 250


class _SearchSignals(QObject):
    """Signals emitted from a background station search back to the GUI thread."""

    done = Signal(list)
    error = Signal(str)


class _SearchTask(QRunnable):
    """Runs one Spansh station search off the GUI thread."""

    def __init__(
        self,
        reference_system: str,
        needed: dict[str, int],
        include_planetary: bool,
    ):
        super().__init__()
        self.signals = _SearchSignals()
        self._ref = reference_system
        self._needed = needed
        self._include_planetary = include_planetary

    def run(self) -> None:
        try:
            results = station_search.search_stations(
                self._ref,
                self._needed,
                include_planetary=self._include_planetary,
            )
        except station_search.StationSearchError as exc:
            self.signals.error.emit(str(exc))
        except Exception as exc:  # pragma: no cover - defensive network guard
            self.signals.error.emit(str(exc))
        else:
            self.signals.done.emit(results)



class _DragBar(QFrame):
    """A header strip that moves the parent window when dragged."""

    def __init__(self, window: QWidget) -> None:
        super().__init__()
        self._window = window
        self._press_offset: QPoint | None = None
        self.setCursor(Qt.SizeAllCursor)

    def mousePressEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.LeftButton:
            self._press_offset = (
                event.globalPosition().toPoint() - self._window.frameGeometry().topLeft()
            )

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        if self._press_offset is not None and event.buttons() & Qt.LeftButton:
            self._window.move(event.globalPosition().toPoint() - self._press_offset)

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        self._press_offset = None
        self._window.persist_geometry()


class _FittedTable(QTableView):
    """A table whose height hint equals its content, so the window can auto-fit.

    The *hint* tracks the row count (for auto-fit), but the policy is Expanding:
    in a manually sized window the table absorbs the surplus/deficit (scrolling
    when short) instead of staying content-sized and floating in the middle.
    """

    def __init__(self) -> None:
        super().__init__()
        self.cap = 10_000
        self.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Expanding)
        # Subtle zebra striping (shade set in the theme stylesheet) so busy
        # lists stay readable.
        self.setAlternatingRowColors(True)

    def _content_height(self) -> int:
        model = self.model()
        rows = model.rowCount() if model else 0
        row_h = self.verticalHeader().defaultSectionSize()
        header = self.horizontalHeader()
        head_h = header.height() or header.sizeHint().height()
        frame = 2 * self.frameWidth()
        return head_h + max(1, rows) * row_h + frame

    def sizeHint(self) -> QSize:
        width = super().sizeHint().width()
        return QSize(width, min(self._content_height(), self.cap))

    def minimumSizeHint(self) -> QSize:
        row_h = self.verticalHeader().defaultSectionSize()
        head_h = self.horizontalHeader().sizeHint().height()
        return QSize(0, head_h + row_h)


class OverlayWindow(QWidget):
    settings_requested = Signal()
    quit_requested = Signal()
    carrier_changed = Signal()  # user edited tracked carrier cargo -> persist
    project_removed = Signal()  # user removed a project from a tab -> persist
    game_focus_changed = Signal(bool)  # the game window gained/lost focus

    def __init__(self, config: Config) -> None:
        super().__init__()
        self.config = config
        self._project_ids: list[int] | None = None  # None -> force first rebuild
        self._state: AppState | None = None
        self._current_rows: list[CommodityRow] = []
        self._click_through_active = False
        self._game_focused = False
        # Session flag: cleared when the user grabs the resize grip, so a
        # manually chosen height isn't snapped back by the next auto-fit.
        # Re-applying Settings re-arms it.
        self._auto_fit = True
        # Station-search state.
        self._search_pool = QThreadPool(self)
        self._search_pool.setMaxThreadCount(1)  # one search at a time
        self._search_seq = 0
        self._searching = False
        self._stations_loaded_key: tuple | None = None
        self._search_task = None  # holds the in-flight _SearchTask reference
        self.setWindowTitle("EDSC")
        self.setAttribute(Qt.WA_TranslucentBackground)
        self._apply_window_flags()

        self.panel = QFrame(self)
        self.panel.setObjectName("panel")
        outer = QVBoxLayout(self)
        outer.setContentsMargins(6, 6, 6, 6)
        outer.addWidget(self.panel)

        self._build_ui()
        self.apply_appearance()
        self.resize(config.overlay_width, config.overlay_height)
        self.move(config.overlay_x, config.overlay_y)

        # Focus-driven click-through: poll which window is foreground and, while
        # the game is focused, let the mouse pass through to it.
        self._detector = foreground.make_detector()
        if not self._detector.available:
            self.ghost_btn.setChecked(False)
            self.ghost_btn.setEnabled(False)
            self.ghost_btn.setToolTip(
                "Auto click-through isn't available in this session"
            )
        self._focus_timer = QTimer(self)
        self._focus_timer.setInterval(_FOCUS_POLL_MS)
        self._focus_timer.timeout.connect(self._update_focus_state)
        self._focus_timer.start()

        # Tab switching. These fire while the overlay is focused; a global X11
        # hotkey (installed by the app) covers switching while in-game.
        for seq, slot in (
            ("Ctrl+Shift+Left", self.select_prev_tab),
            ("Ctrl+Shift+Right", self.select_next_tab),
        ):
            sc = QShortcut(QKeySequence(seq), self)
            sc.setContext(Qt.ApplicationShortcut)
            sc.activated.connect(slot)

    #  construction

    def _build_ui(self) -> None:
        root = QVBoxLayout(self.panel)
        root.setContentsMargins(12, 10, 12, 10)
        root.setSpacing(6)

        # Header: title + window buttons, draggable.
        self.header = _DragBar(self)
        header_row = QHBoxLayout(self.header)
        header_row.setContentsMargins(0, 0, 0, 0)
        titles = QVBoxLayout()
        titles.setSpacing(0)
        self.title_label = QLabel("EDSC — Supply Chain")
        self.title_label.setObjectName("title")
        self.subtitle_label = QLabel("")
        self.subtitle_label.setObjectName("subtitle")
        titles.addWidget(self.title_label)
        titles.addWidget(self.subtitle_label)
        header_row.addLayout(titles, 1)

        self.pin_btn = self._tool("▲", "Keep above other windows", checkable=True)
        self.pin_btn.setChecked(self.config.always_on_top)
        self.pin_btn.toggled.connect(self._toggle_pin)
        self.ghost_btn = self._tool(
            "▨", "Auto click-through while the game is focused", checkable=True
        )
        self.ghost_btn.setChecked(self.config.auto_click_through)
        self.ghost_btn.toggled.connect(self._toggle_auto_click_through)
        self.settings_btn = self._tool("⚙", "Settings")
        self.settings_btn.clicked.connect(self.settings_requested.emit)
        self.hide_btn = self._tool("—", "Hide to tray")
        self.hide_btn.clicked.connect(self.hide)
        for b in (self.pin_btn, self.ghost_btn, self.settings_btn, self.hide_btn):
            header_row.addWidget(b)
        root.addWidget(self.header)

        # One tab per construction, plus a combined "All" tab. Visible only when
        # there are at least two constructions to switch between.
        self.tabs = QTabBar()
        self.tabs.setExpanding(False)
        self.tabs.setDrawBase(False)
        self.tabs.setElideMode(Qt.ElideRight)
        self.tabs.setUsesScrollButtons(True)
        self.tabs.currentChanged.connect(self._on_tab_changed)
        # Right-click a project tab to remove it (finished/abandoned sites
        # otherwise accumulate forever; docking there again re-adds it).
        self.tabs.setContextMenuPolicy(Qt.CustomContextMenu)
        self.tabs.customContextMenuRequested.connect(self._tab_context_menu)
        root.addWidget(self.tabs)

        # Two interchangeable pages: the commodity list and the station search.
        # They share the same slot; only one is visible at a time so the window
        # can auto-fit its height to whichever list is showing. Stretch 1: the
        # visible page (whose table is its own stretch element) absorbs any
        # extra height, keeping the header/tabs pinned to the top and the
        # footer rows pinned to the bottom.
        self.commodity_page = self._build_commodity_page()
        self.stations_page = self._build_stations_page()
        self.stations_page.setVisible(False)
        root.addWidget(self.commodity_page, 1)
        root.addWidget(self.stations_page, 1)

        # Shared bottom: the status line, then a credit footer with the grip.
        self.status_label = QLabel("Starting…")
        self.status_label.setObjectName("status")
        self.status_label.setWordWrap(True)
        root.addWidget(self.status_label)

        credit_row = QHBoxLayout()
        self.credit_label = QLabel(f"EDSC {__version__} · by CMDR FEEDMEWEED 2026")
        self.credit_label.setObjectName("credit")
        credit_row.addWidget(self.credit_label, 1)
        # Grabbing the grip means the user wants this size: stop auto-fitting
        # for the session (the event filter clears the flag on press).
        self._size_grip = QSizeGrip(self)
        self._size_grip.installEventFilter(self)
        credit_row.addWidget(self._size_grip, 0, Qt.AlignBottom | Qt.AlignRight)
        root.addLayout(credit_row)

    def _build_commodity_page(self) -> QWidget:
        page = QWidget()
        root = QVBoxLayout(page)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(6)

        # Progress line.
        prog_row = QHBoxLayout()
        self.progress = QProgressBar()
        self.progress.setRange(0, 100)
        self.progress.setTextVisible(False)
        self.percent_label = QLabel("0%")
        prog_row.addWidget(self.progress, 1)
        prog_row.addWidget(self.percent_label)
        root.addLayout(prog_row)

        # Commodity table.
        self.model = CommodityTableModel()
        self.model.set_hide_completed(self.config.hide_completed)
        self.table = _FittedTable()
        self.table.setModel(self.model)
        self.table.setShowGrid(False)
        self.table.setSelectionMode(QTableView.NoSelection)
        self.table.setFocusPolicy(Qt.NoFocus)
        self.table.verticalHeader().setVisible(False)
        self.table.verticalHeader().setDefaultSectionSize(20)
        hdr = self.table.horizontalHeader()
        hdr.setSectionResizeMode(0, QHeaderView.Stretch)
        for c in (1, 2, 3, 4, 5):
            hdr.setSectionResizeMode(c, QHeaderView.ResizeToContents)
        # Stretch 1: the list takes any extra height, so the progress bar stays
        # at the top and the footer/carrier rows stay at the bottom.
        root.addWidget(self.table, 1)

        # Footer: totals + toggles.
        footer = QHBoxLayout()
        self.totals_label = QLabel("")
        self.totals_label.setObjectName("subtitle")
        footer.addWidget(self.totals_label, 1)
        self.carrier_btn = self._tool("FC…", "Set fleet-carrier cargo amounts")
        self.carrier_btn.clicked.connect(self._edit_carrier_cargo)
        footer.addWidget(self.carrier_btn)
        self.hide_done_btn = self._tool("Hide done", "Hide fully delivered items",
                                        checkable=True)
        self.hide_done_btn.setChecked(self.config.hide_completed)
        self.hide_done_btn.toggled.connect(self._toggle_hide_done)
        footer.addWidget(self.hide_done_btn)
        root.addLayout(footer)

        # Fleet-carrier tracking summary (hidden until a carrier is known).
        self.carrier_label = QLabel("")
        self.carrier_label.setObjectName("status")
        self.carrier_label.setWordWrap(True)
        root.addWidget(self.carrier_label)
        return page

    def _build_stations_page(self) -> QWidget:
        page = QWidget()
        root = QVBoxLayout(page)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(6)

        # Controls: reference system + refresh. The search is always restricted
        # to large-pad stations, so there's no pad filter to toggle.
        bar = QHBoxLayout()
        self.stations_ref_label = QLabel("")
        self.stations_ref_label.setObjectName("subtitle")
        bar.addWidget(self.stations_ref_label, 1)
        self.planets_btn = self._tool(
            "Include planets",
            "Include planetary outposts in the results",
            checkable=True,
        )
        self.planets_btn.setChecked(self.config.stations_include_planets)
        self.planets_btn.toggled.connect(self._toggle_include_planets)
        bar.addWidget(self.planets_btn)
        self.refresh_btn = self._tool("↻ Search", "Search Spansh for nearby stations")
        self.refresh_btn.clicked.connect(self._refresh_stations)
        bar.addWidget(self.refresh_btn)
        root.addLayout(bar)

        # Station results table.
        self.stations_model = StationTableModel()
        self.stations_table = _FittedTable()
        self.stations_table.setModel(self.stations_model)
        self.stations_table.setShowGrid(False)
        self.stations_table.setSelectionMode(QTableView.NoSelection)
        self.stations_table.setFocusPolicy(Qt.NoFocus)
        self.stations_table.verticalHeader().setVisible(False)
        self.stations_table.verticalHeader().setDefaultSectionSize(20)
        shdr = self.stations_table.horizontalHeader()
        shdr.setSectionResizeMode(0, QHeaderView.Stretch)
        shdr.setSectionResizeMode(1, QHeaderView.Stretch)
        for c in (2, 3, 4):
            shdr.setSectionResizeMode(c, QHeaderView.ResizeToContents)
        root.addWidget(self.stations_table, 1)

        self.stations_status = QLabel("")
        self.stations_status.setObjectName("status")
        self.stations_status.setWordWrap(True)
        root.addWidget(self.stations_status)
        return page


    def _tool(self, text: str, tip: str, checkable: bool = False) -> QToolButton:
        b = QToolButton()
        b.setText(text)
        b.setToolTip(tip)
        b.setCheckable(checkable)
        b.setCursor(Qt.PointingHandCursor)
        return b

    #  appearance / flags 

    def _apply_window_flags(self) -> None:
        flags = Qt.FramelessWindowHint | Qt.Tool
        if self.config.always_on_top:
            # Keeps the overlay above normal + borderless-fullscreen windows.
            # NOTE: no external window can cover *exclusive* fullscreen (the
            # compositor unredirects it); users must run ED in Borderless. See
            # the "Display mode" section of the README.
            flags |= Qt.WindowStaysOnTopHint
        self.setWindowFlags(flags)

    def apply_appearance(self) -> None:
        self.panel.setStyleSheet(
            theme.panel_stylesheet(
                alpha=int(self.config.overlay_opacity * 255),
                font_pt=self.config.font_point_size,
            )
        )

    def sync_from_config(self) -> None:
        """Re-apply settings that changed via the Settings dialog."""
        was_visible = self.isVisible()
        pos = self.pos()
        self.pin_btn.blockSignals(True)
        self.pin_btn.setChecked(self.config.always_on_top)
        self.pin_btn.blockSignals(False)
        self._apply_window_flags()
        self.move(pos)
        if was_visible:
            self.show()

        self.ghost_btn.blockSignals(True)
        self.ghost_btn.setChecked(
            self.config.auto_click_through and self._detector.available
        )
        self.ghost_btn.blockSignals(False)
        self._update_focus_state()
        self._auto_fit = True  # applying Settings re-arms height auto-fit
        if self._state is not None:
            self._fit_height()

    #  external API -

    def set_status(self, text: str) -> None:
        self.status_label.setText(text)

    def refresh(self, state: AppState) -> None:
        """Re-render from the current app state (called on every change)."""
        self._state = state
        projects = state.project_list()
        ids = [p.market_id for p in projects]

        if ids != self._project_ids:
            self._rebuild_tabs(projects)
            self._project_ids = ids

        self._render_current(state)
        self._fit_height()

    #  tabs -

    def _rebuild_tabs(self, projects: list[Project]) -> None:
        """One tab per construction, prefixed with a combined 'All' tab.

        The tab bar is shown whenever there's at least one construction. The
        combined "All" tab is added once there are two or more (with a single
        construction it would just duplicate that one). Each tab stores its
        market id (or the combined sentinel) as tab data.
        """
        self.tabs.blockSignals(True)
        while self.tabs.count():
            self.tabs.removeTab(0)

        if projects:
            if len(projects) >= 2:
                self.tabs.addTab("All")
                self.tabs.setTabData(0, COMBINED_MARKET_ID)
                self.tabs.setTabToolTip(0, "Combined needs across all constructions")
            for p in projects:
                idx = self.tabs.addTab(self._tab_label(p))
                self.tabs.setTabData(idx, p.market_id)
                self.tabs.setTabToolTip(idx, p.title)
            # Trailing "Stations" tab: nearest markets selling what you still need.
            sidx = self.tabs.addTab("⚑ Stations")
            self.tabs.setTabData(sidx, STATIONS_MARKET_ID)
            self.tabs.setTabToolTip(
                sidx, "Nearest stations selling the commodities you still need"
            )
            self._select_market(self.config.selected_market_id)

        self.tabs.setVisible(bool(projects))
        self.tabs.blockSignals(False)

    def _tab_label(self, project: Project) -> str:
        name = project.station_name or project.title
        for prefix in (
            "Orbital Construction Site: ",
            "Planetary Construction Site: ",
            "Construction Site: ",
        ):
            if name.startswith(prefix):
                name = name[len(prefix):]
                break
        if project.complete:
            return "✔ " + name
        if project.failed:
            return "✖ " + name
        return name

    def _tab_context_menu(self, pos: QPoint) -> None:
        """Offer to remove the project under the cursor (real projects only)."""
        idx = self.tabs.tabAt(pos)
        if idx < 0 or self._state is None:
            return
        mid = self.tabs.tabData(idx)
        if mid in (COMBINED_MARKET_ID, STATIONS_MARKET_ID):
            return
        proj = self._state.projects.get(mid)
        if proj is None:
            return
        menu = QMenu(self)
        remove = menu.addAction(f"Remove “{proj.title}”")
        if menu.exec(self.tabs.mapToGlobal(pos)) is remove:
            self._state.remove_project(mid)
            self.project_removed.emit()
            self.refresh(self._state)

    @property
    def focus_detection_available(self) -> bool:
        """Whether game-window focus can be detected in this session."""
        return self._detector.available

    def _select_market(self, market_id) -> None:
        """Select the tab for a market id, defaulting to the first tab."""
        for i in range(self.tabs.count()):
            if self.tabs.tabData(i) == market_id:
                self.tabs.setCurrentIndex(i)
                return
        self.tabs.setCurrentIndex(0)

    def _selected_market(self):
        idx = self.tabs.currentIndex()
        return self.tabs.tabData(idx) if idx >= 0 else None

    def _on_tab_changed(self, _index: int) -> None:
        mid = self._selected_market()
        if mid is not None:
            self.config.selected_market_id = mid
        if self._state is not None:
            self._render_current(self._state)
            self._fit_height()

    def select_prev_tab(self) -> None:
        self._step_tab(-1)

    def select_next_tab(self) -> None:
        self._step_tab(+1)

    def _step_tab(self, delta: int) -> None:
        count = self.tabs.count()
        if count > 1:
            self.tabs.setCurrentIndex((self.tabs.currentIndex() + delta) % count)

    #  rendering helpers 

    def _render_current(self, state: AppState) -> None:
        mid = self._selected_market() if self.tabs.isVisible() else None
        if mid == STATIONS_MARKET_ID:
            self._render_stations(state)
            return
        self._show_page(stations=False)
        if mid == COMBINED_MARKET_ID:
            active = state.active_projects()
            subtitle = f"{len(active)} constructions"
            self._render_project(state.combined_project(), state, subtitle=subtitle)
            return
        if mid is not None and mid in state.projects:
            self._render_project(state.projects[mid], state)
            return
        # Tab bar hidden (0 or 1 project): show the single project, or empty.
        projects = state.project_list()
        if projects:
            self._render_project(projects[0], state)
        else:
            self._render_empty()

    def _render_project(
        self, proj: Project, state: AppState, subtitle: str | None = None
    ) -> None:
        status = " · COMPLETE" if proj.complete else (" · FAILED" if proj.failed else "")
        self.title_label.setText(proj.station_name or proj.title)
        if subtitle is None:
            subtitle = (proj.system_name or "") + status
        self.subtitle_label.setText(subtitle)

        frac = proj.progress_fraction()
        self.progress.setValue(int(round(frac * 100)))
        self.percent_label.setText(f"{frac * 100:.0f}%")

        self._current_rows = proj.rows(state.cargo, state.carrier_cargo)
        self.model.set_rows(self._current_rows)

        delivered = proj.total_provided()
        required = proj.total_required()
        remaining = required - delivered
        self.totals_label.setText(
            f"{delivered:,} / {required:,} t delivered · {remaining:,} t to go"
        )
        self._update_carrier_label(state)

    def _render_empty(self) -> None:
        self.title_label.setText("No construction projects yet")
        self.subtitle_label.setText("Dock at a colonisation construction site")
        self.progress.setValue(0)
        self.percent_label.setText("0%")
        self._current_rows = []
        self.model.set_rows([])
        self.totals_label.setText("")
        self._update_carrier_label(self._state)

    #  nearest stations -

    def _show_page(self, *, stations: bool) -> None:
        """Show either the station-search page or the commodity page."""
        self.stations_page.setVisible(stations)
        self.commodity_page.setVisible(not stations)

    def _render_stations(self, state: AppState) -> None:
        """Switch to the station-search page and (re)run the search as needed."""
        self._show_page(stations=True)
        self.title_label.setText("Nearest stations")
        system = state.current_system or "unknown"
        self.subtitle_label.setText(f"Buying near {system}")
        needs = state.outstanding_needs()
        ref_text = f"Near {system} · {len(needs)} commodities needed"
        if len(needs) > station_search.MAX_COMMODITIES:
            ref_text += (
                f" · querying top {station_search.MAX_COMMODITIES} by shortfall"
            )
        self.stations_ref_label.setText(ref_text)

        if not state.current_system:
            self.stations_status.setText(
                "Waiting for your location — jump or dock so EDSC knows where you are."
            )
            self.stations_model.set_rows([])
            return
        if not needs:
            self.stations_status.setText("Nothing outstanding — all commodities covered.")
            self.stations_model.set_rows([])
            return

        key = self._station_search_key(state)
        if not self._searching and key != self._stations_loaded_key:
            self._start_station_search(state)

    def _station_search_key(self, state: AppState) -> tuple:
        needs = state.outstanding_needs()
        return (
            state.current_system,
            frozenset(needs.keys()),
            self.config.stations_include_planets,
        )

    def _refresh_stations(self) -> None:
        """Manual refresh button: force a fresh search."""
        if self._state is not None:
            self._stations_loaded_key = None
            self._start_station_search(self._state)

    def _toggle_include_planets(self, checked: bool) -> None:
        """'Include planets' toggle: re-search with the new surface filter."""
        self.config.stations_include_planets = checked
        self._refresh_stations()

    def _start_station_search(self, state: AppState) -> None:
        needs = state.outstanding_needs()
        if not state.current_system or not needs:
            return
        self._searching = True
        self._search_seq += 1
        seq = self._search_seq
        self.stations_status.setText(f"Searching Spansh near {state.current_system}…")
        self.refresh_btn.setEnabled(False)
        # Pass the amounts too: a station only counts as stocking a commodity
        # when its supply covers (a useful chunk of) the shortfall.
        task = _SearchTask(
            state.current_system,
            dict(needs),
            self.config.stations_include_planets,
        )
        # Keep a Python reference so the task and its signal object aren't
        # garbage-collected before the queued result reaches the GUI thread.
        task.setAutoDelete(False)
        self._search_task = task
        task.signals.done.connect(
            lambda results, s=seq, k=self._station_search_key(state):
            self._on_search_done(s, k, results)
        )
        task.signals.error.connect(
            lambda message, s=seq: self._on_search_error(s, message)
        )
        self._search_pool.start(task)

    def _on_search_done(self, seq: int, key: tuple, results: list) -> None:
        if seq != self._search_seq:
            return  # a newer search superseded this one
        self._searching = False
        self._stations_loaded_key = key
        self.refresh_btn.setEnabled(True)
        self.stations_model.set_rows(results)
        if results:
            best = results[0]
            self.stations_status.setText(
                f"{len(results)} stations · best {best.match_count}/"
                f"{best.needed_total} at {best.distance_ly:,.1f} ly"
            )
        else:
            self.stations_status.setText(
                "No stations found selling those commodities nearby."
            )
        self._fit_height()

    def _on_search_error(self, seq: int, message: str) -> None:
        if seq != self._search_seq:
            return
        self._searching = False
        self.refresh_btn.setEnabled(True)
        self.stations_status.setText(f"Search failed: {message}")

    def _update_carrier_label(self, state: AppState | None) -> None:
        """Show tracked carrier tonnage, flagging when it disagrees with the
        authoritative CarrierStats total."""
        if state is None or (not state.carrier_callsign and not state.carrier_cargo):
            self.carrier_label.setVisible(False)
            self.carrier_btn.setVisible(bool(state and state.projects))
            return
        tracked = state.carrier_tracked_total()
        who = f"FC {state.carrier_callsign}" if state.carrier_callsign else "Fleet carrier"
        text = f"{who} · tracking {tracked:,} t"
        if state.carrier_total and tracked > state.carrier_total:
            # Tracking more than the carrier holds is a definite error (e.g.
            # stock sold via the carrier market, which journals don't report
            # as a transfer): the table would show phantom coverage.
            text += (
                f" — exceeds the carrier's {state.carrier_total:,} t;"
                " 'FC…' to correct"
            )
        elif state.carrier_total and tracked < state.carrier_total:
            # Usually just unrelated cargo (trade goods etc.); informational.
            text += f" · {state.carrier_total - tracked:,} t aboard untracked"
        self.carrier_label.setText(text)
        self.carrier_label.setVisible(True)
        self.carrier_btn.setVisible(True)

    def _edit_carrier_cargo(self) -> None:
        """Open the manual carrier-cargo dialog for the commodities in view."""
        if self._state is None or not self._current_rows:
            return
        dialog = CarrierCargoDialog(self._current_rows, self)
        if dialog.exec():
            for key, amount in dialog.values().items():
                self._state.set_carrier_amount(key, amount)
            self.carrier_changed.emit()
            self.refresh(self._state)

    #  toggles 

    def _toggle_pin(self, checked: bool) -> None:
        self.config.always_on_top = checked
        pos = self.pos()
        self._apply_window_flags()
        self.move(pos)
        self.show()  # re-applying flags requires a re-show

    def _toggle_auto_click_through(self, checked: bool) -> None:
        self.config.auto_click_through = checked
        if checked:
            self._update_focus_state()  # apply immediately
        else:
            self._set_click_through(False)  # always interactive / movable

    def _update_focus_state(self) -> None:
        """Timer slot: track game focus; click-through only while it's focused.

        Game focus is computed regardless of the click-through toggle because
        other consumers (global hotkey grabs) key off it too.
        """
        game_focused = False
        if self._detector.available:
            info = self._detector.active()
            game_focused = info is not None and info.matches(
                self.config.game_window_matchers
            )
        if game_focused != self._game_focused:
            self.game_focus_changed.emit(game_focused)
        self._set_click_through(game_focused and self.config.auto_click_through)
        # When the game grabs focus a window manager may restack it over us;
        # re-assert keep-above so the overlay stays visible over a (borderless)
        # game window. Only on the rising edge to avoid fighting the WM.
        if game_focused and not self._game_focused and self.config.always_on_top:
            topmost.assert_above(self)
        self._game_focused = game_focused

    def _set_click_through(self, enabled: bool) -> None:
        if enabled == self._click_through_active:
            return
        clickthrough.set_click_through(self, enabled)
        self._click_through_active = enabled

    #  auto height 

    def eventFilter(self, obj, event) -> bool:
        if obj is self._size_grip and event.type() == QEvent.MouseButtonPress:
            self._auto_fit = False  # the user is choosing a size; respect it
        return super().eventFilter(obj, event)

    def _fit_height(self) -> None:
        """Size the window's height to the number of visible rows in view."""
        if not self.config.auto_height or not self._auto_fit:
            return
        table = self.stations_table if self.stations_page.isVisible() else self.table
        # Cap growth so a huge list scrolls instead of leaving the screen.
        screen = self.screen()
        avail = screen.availableGeometry().height() if screen else 1000
        # Leave room for the non-table chrome (header, progress, footer...).
        table.cap = max(60, int(avail * 0.8) - 180)

        # Activate the nested layouts so the top-level size hint reflects the new
        # row count immediately (without waiting for the event loop to relayout).
        table.updateGeometry()
        self.panel.layout().activate()
        self.layout().activate()

        target = self.sizeHint().height()
        if self.height() != target:
            self.resize(self.width(), target)

    def _toggle_hide_done(self, checked: bool) -> None:
        self.config.hide_completed = checked
        self.model.set_hide_completed(checked)
        if self._state is not None:
            self.refresh(self._state)

    #  geometry persistence 

    def persist_geometry(self) -> None:
        self.config.overlay_x = self.x()
        self.config.overlay_y = self.y()
        self.config.overlay_width = self.width()
        self.config.overlay_height = self.height()

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self.persist_geometry()

    def stop(self) -> None:
        """Release focus-watching resources (called on shutdown)."""
        self._focus_timer.stop()
        self._detector.close()

    def closeEvent(self, event) -> None:
        # Window-manager close (e.g. Alt+F4) means quit; the header "-" button
        # only hides to tray instead.
        self.persist_geometry()
        self.stop()
        self.quit_requested.emit()
        event.accept()
