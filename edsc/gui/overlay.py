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
    QModelIndex,
    QObject,
    QPoint,
    QRunnable,
    QSize,
    Qt,
    QThreadPool,
    QTimer,
    Signal,
)
from PySide6.QtGui import (
    QFontMetrics,
    QGuiApplication,
    QKeySequence,
    QMouseEvent,
    QShortcut,
)
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
from .table_model import (
    STATION_COLUMNS,
    ST_ARRIVAL_COL,
    ST_COVER_COL,
    ST_DIST_COL,
    ST_MATCH_COL,
    ST_SYSTEM_COL,
    CommodityTableModel,
    StationTableModel,
)

# How often to check which window is focused (ms).
_FOCUS_POLL_MS = 250

# Each visible station table is capped to ten mixed-category entries. The
# underlying cache still retains ten orbitals, ten planetary stations, and ten
# carriers for instant re-filtering and completeness checks.
_RESULTS_SHOWN = 10


class _SearchSignals(QObject):
    """Signals emitted from a background station search back to the GUI thread."""

    done = Signal(list)
    error = Signal(str)


class _SearchTask(QRunnable):
    """Fetch one complete, reusable Spansh result pool off the GUI thread."""

    def __init__(
        self,
        reference_system: str,
        needed: dict[str, int],
        recent_only: bool,
    ):
        super().__init__()
        self.signals = _SearchSignals()
        self._ref = reference_system
        self._needed = needed
        self._recent_only = recent_only
        self._cancelled = False

    def cancel(self) -> None:
        """Tell a running search to discard its result instead of emitting."""
        self._cancelled = True

    def _emit(self, name: str, *payload) -> None:
        if self._cancelled:
            return
        try:
            getattr(self.signals, name).emit(*payload)
        except RuntimeError:
            # The signals QObject was deleted under us (app quit mid-search);
            # nobody is listening any more, so just drop the result.
            pass

    def run(self) -> None:
        try:
            results = station_search.search_stations(
                self._ref,
                self._needed,
                recent_only=self._recent_only,
            )
        except station_search.StationSearchError as exc:
            self._emit("error", str(exc))
        except Exception as exc:  # pragma: no cover - defensive network guard
            self._emit("error", str(exc))
        else:
            self._emit("done", results)



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
        self.cap = theme.METRICS.table_height_cap
        self.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Expanding)

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


class _StationTable(_FittedTable):
    """Station results table that lays out its own column widths.

    The numeric columns are constant (set via ``fixed_widths``); the station
    and system names share whatever is left, station first: the system column
    only grows past its small elided budget with space the station name
    doesn't need, up to showing full system names, and any surplus beyond
    that goes back to the station column.
    """

    def __init__(self) -> None:
        super().__init__()
        self.fixed_widths: dict[int, int] = {}  # numeric col -> constant px
        # Elided floors; real values set from font metrics. System yields
        # space before the station name, so it collapses to the smaller floor.
        self.system_min = theme.METRICS.station_system_width_fallback
        self.station_min = theme.METRICS.station_name_width_fallback

    def relayout_columns(self) -> None:
        if not self.fixed_widths or self.model() is None:
            return
        hdr = self.horizontalHeader()
        for col, width in self.fixed_widths.items():
            hdr.resizeSection(col, width)
        avail = self.viewport().width() - sum(self.fixed_widths.values())
        # sizeHintForColumn under-reserves the delegate's text margins by a
        # few pixels, which would elide the last character even with room to
        # spare - pad the "shows the full name" targets past that.
        slack = theme.METRICS.station_column_slack
        station_full = max(0, self.sizeHintForColumn(0)) + slack
        system_full = max(0, self.sizeHintForColumn(ST_SYSTEM_COL)) + slack
        # The station name is the priority column: the system column gives up
        # its width first. Keep the station at its full width and shrink system
        # toward its floor; only once system sits at that floor does the
        # station name itself start eliding, down to its own floor.
        system_w = max(self.system_min, min(system_full, avail - station_full))
        station_w = max(self.station_min, avail - system_w)
        # Too narrow even for both floors: clip system further so the station
        # keeps its floor (the trailing numeric columns clip, as elsewhere).
        if station_w + system_w > avail:
            system_w = max(0, avail - station_w)
        hdr.resizeSection(ST_SYSTEM_COL, system_w)
        hdr.resizeSection(0, station_w)

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self.relayout_columns()


class _ElideLabel(QLabel):
    """A label that elides with … instead of dictating a minimum width.

    A plain QLabel refuses to shrink below its full text, so one long status
    line would set the whole window's minimum width. This one lets the layout
    squeeze it and shows however much fits (the full text moves to a tooltip).
    """

    def __init__(self) -> None:
        super().__init__()
        self._full_text = ""

    def setText(self, text: str) -> None:
        self._full_text = text
        self.setToolTip(text)
        self._relide()

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._relide()

    def _relide(self) -> None:
        metrics = self.fontMetrics()
        super().setText(
            metrics.elidedText(self._full_text, Qt.ElideRight, max(0, self.width()))
        )

    def minimumSizeHint(self) -> QSize:
        return QSize(0, super().minimumSizeHint().height())


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
        # Spansh is queried automatically only once per session (the first time
        # the tab is viewed with a known location and outstanding needs); after
        # that only ↻ Search or the Recent pre-search toggle re-runs it. Every
        # station-type control filters this cached pool locally.
        self._stations_searched = False
        self._search_task = None  # holds the in-flight _SearchTask reference
        self._search_ref = ""  # reference system behind the last search
        self._search_needs: dict[str, int] = {}
        self._search_recent_only = False
        self._station_results: list[station_search.StationResult] = []
        self.setWindowTitle("EDSC")
        self.setAttribute(Qt.WA_TranslucentBackground)
        self._apply_window_flags()

        self.panel = QFrame(self)
        theme.set_role(self.panel, theme.PANEL_ROLE)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(*theme.METRICS.overlay_outer_margins)
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
        root.setContentsMargins(*theme.METRICS.panel_margins)
        root.setSpacing(theme.METRICS.content_spacing)

        # Header: title + window buttons, draggable.
        self.header = _DragBar(self)
        header_row = QHBoxLayout(self.header)
        header_row.setContentsMargins(*theme.METRICS.page_margins)
        titles = QVBoxLayout()
        titles.setSpacing(theme.METRICS.header_spacing)
        # Elided: a long construction-site name must not dictate how narrow
        # the window can go (the minimum must not depend on tab content).
        self.title_label = _ElideLabel()
        theme.set_role(self.title_label, theme.TITLE_ROLE)
        self.title_label.setText("EDSC - Supply Chain")
        self.subtitle_label = _ElideLabel()
        theme.set_role(self.subtitle_label, theme.SUBTITLE_ROLE)
        titles.addWidget(self.title_label)
        titles.addWidget(self.subtitle_label)
        header_row.addLayout(titles, 1)

        self.pin_btn = self._window_control(
            "▲", "Keep above other windows", checkable=True
        )
        self.pin_btn.setChecked(self.config.always_on_top)
        self.pin_btn.toggled.connect(self._toggle_pin)
        self.ghost_btn = self._window_control(
            "▨", "Auto click-through while the game is focused", checkable=True
        )
        self.ghost_btn.setChecked(self.config.auto_click_through)
        self.ghost_btn.toggled.connect(self._toggle_auto_click_through)
        self.settings_btn = self._window_control("⚙", "Settings")
        self.settings_btn.clicked.connect(self.settings_requested.emit)
        self.hide_btn = self._window_control("-", "Hide to tray")
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
        theme.set_role(self.status_label, theme.STATUS_ROLE)
        self.status_label.setWordWrap(True)
        root.addWidget(self.status_label)

        credit_row = QHBoxLayout()
        self.credit_label = QLabel(f"EDSC {__version__} · by CMDR FEEDMEWEED 2026")
        theme.set_role(self.credit_label, theme.CREDIT_ROLE)
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
        root.setContentsMargins(*theme.METRICS.page_margins)
        root.setSpacing(theme.METRICS.content_spacing)

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
        theme.configure_table(self.table)
        self.table.setSelectionMode(QTableView.NoSelection)
        self.table.setFocusPolicy(Qt.NoFocus)
        self.table.verticalHeader().setVisible(False)
        hdr = self.table.horizontalHeader()
        hdr.setSectionResizeMode(0, QHeaderView.Stretch)
        for c in (1, 2, 3, 4, 5):
            hdr.setSectionResizeMode(c, QHeaderView.ResizeToContents)
        # Stretch 1: the list takes any extra height, so the progress bar stays
        # at the top and the footer/carrier rows stay at the bottom.
        root.addWidget(self.table, 1)

        # Shown only when every commodity of the viewed construction has been
        # delivered: clicking removes the finished project (and its tab) so
        # completed sites don't linger in the overlay.
        self.complete_btn = self._tool(
            "✔ Complete construction",
            "All commodities delivered - remove this construction from the overlay",
        )
        theme.set_role(self.complete_btn, theme.COMPLETE_BUTTON_ROLE)
        self.complete_btn.clicked.connect(self._complete_construction)
        self.complete_btn.setVisible(False)
        root.addWidget(self.complete_btn)

        # Footer: totals + toggles. The totals elide like the header labels:
        # every text that varies with project data must stay out of the
        # minimum-width calculation.
        footer = QHBoxLayout()
        self.totals_label = _ElideLabel()
        theme.set_role(self.totals_label, theme.SUBTITLE_ROLE)
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
        theme.set_role(self.carrier_label, theme.STATUS_ROLE)
        self.carrier_label.setWordWrap(True)
        root.addWidget(self.carrier_label)
        return page

    def _build_stations_page(self) -> QWidget:
        page = QWidget()
        root = QVBoxLayout(page)
        root.setContentsMargins(*theme.METRICS.page_margins)
        root.setSpacing(theme.METRICS.content_spacing)

        # Controls: reference system + refresh. The search is always restricted
        # to large-pad stations, so there's no pad filter to toggle. The label
        # elides so its (long) text never widens the window's minimum.
        bar = QHBoxLayout()
        self.stations_ref_label = _ElideLabel()
        theme.set_role(self.stations_ref_label, theme.SUBTITLE_ROLE)
        bar.addWidget(self.stations_ref_label, 1)
        self.planets_btn = self._tool(
            "Planets",
            "Add planetary outposts to the orbital-station results",
            checkable=True,
        )
        self.planets_btn.setChecked(self.config.stations_include_planets)
        self.planets_btn.toggled.connect(self._toggle_include_planets)
        bar.addWidget(self.planets_btn)
        self.carriers_btn = self._tool(
            "Carriers",
            "Add fleet carriers to the orbital-station results",
            checkable=True,
        )
        self.carriers_btn.setChecked(self.config.stations_include_carriers)
        self.carriers_btn.toggled.connect(self._toggle_include_carriers)
        bar.addWidget(self.carriers_btn)
        self.recent_btn = self._tool(
            "Recent",
            "Search only markets updated in the last 24 hours (runs a new search)",
            checkable=True,
        )
        self.recent_btn.setChecked(self.config.stations_recent_only)
        self.recent_btn.toggled.connect(self._toggle_recent)
        bar.addWidget(self.recent_btn)
        self.refresh_btn = self._tool("↻ Search", "Search Spansh for nearby stations")
        self.refresh_btn.clicked.connect(self._refresh_stations)
        bar.addWidget(self.refresh_btn)
        root.addLayout(bar)

        # Station results table.
        self.stations_model = StationTableModel()
        self.stations_table = _StationTable()
        self._init_station_table(self.stations_table, self.stations_model)
        root.addWidget(self.stations_table, 1)

        # Secondary table: stations covering the residual demand the best
        # station can't fully supply. Its independent filters are applied to the
        # same cached result pool, so broadening it is instant and performs no I/O.
        self.stations_more_bar = QWidget()
        more_bar = QHBoxLayout(self.stations_more_bar)
        more_bar.setContentsMargins(*theme.METRICS.page_margins)
        self.stations_more_label = QLabel("")
        theme.set_role(self.stations_more_label, theme.SUBTITLE_ROLE)
        self.stations_more_label.setWordWrap(True)
        more_bar.addWidget(self.stations_more_label, 1)
        self.planets_btn2 = self._tool(
            "Planets",
            "Add planetary outposts to the orbital remaining-demand results",
            checkable=True,
        )
        self.planets_btn2.setChecked(self.config.stations_include_planets)
        self.planets_btn2.toggled.connect(self._toggle_follow_up_planets)
        more_bar.addWidget(self.planets_btn2)
        self.carriers_btn2 = self._tool(
            "Carriers",
            "Add fleet carriers to the orbital remaining-demand results",
            checkable=True,
        )
        self.carriers_btn2.setChecked(self.config.stations_include_carriers)
        self.carriers_btn2.toggled.connect(self._toggle_follow_up_carriers)
        more_bar.addWidget(self.carriers_btn2)
        self.stations_more_bar.setVisible(False)
        root.addWidget(self.stations_more_bar)

        self.stations_model2 = StationTableModel()
        self.stations_table2 = _StationTable()
        self._init_station_table(self.stations_table2, self.stations_model2)
        self.stations_table2.setVisible(False)
        root.addWidget(self.stations_table2, 1)

        self.stations_status = QLabel("")
        theme.set_role(self.stations_status, theme.STATUS_ROLE)
        self.stations_status.setWordWrap(True)
        root.addWidget(self.stations_status)
        return page

    def _init_station_table(
        self, table: _StationTable, model: StationTableModel
    ) -> None:
        table.setModel(model)
        theme.configure_table(table, elide_text=True)
        table.setSelectionMode(QTableView.NoSelection)
        table.setFocusPolicy(Qt.NoFocus)
        table.verticalHeader().setVisible(False)
        table.clicked.connect(self._copy_station_system)
        # All sections Fixed: relayout_columns owns every width (constant
        # numerics, then station name first, system name with the leftovers).
        table.horizontalHeader().setSectionResizeMode(QHeaderView.Fixed)
        model.modelReset.connect(table.relayout_columns)

    def _apply_station_column_widths(self) -> None:
        """Feed the station tables their column budgets.

        Numeric columns get constant widths sized from font metrics (so the
        font-size setting scales them) to the widest realistic value - numbers
        must never elide. The system column's floor is a small elided budget;
        past that, relayout_columns grows it only with space the station name
        doesn't need.
        """
        pt = self.config.font_point_size
        prop = theme.resized_font(self.font(), pt)
        mono = theme.monospace_font(pt)
        head = theme.resized_font(self.font(), pt - 1)
        head_fm = QFontMetrics(head)
        pad = theme.METRICS.table_cell_padding

        def col_width(col: int, sample: str, fm: QFontMetrics) -> int:
            header_w = (
                head_fm.horizontalAdvance(STATION_COLUMNS[col])
                + theme.METRICS.table_header_extra
            )
            return max(fm.horizontalAdvance(sample) + pad, header_w)

        prop_fm = QFontMetrics(prop)
        mono_fm = QFontMetrics(mono)
        widths = {
            ST_MATCH_COL: col_width(ST_MATCH_COL, "88/88", mono_fm),
            ST_COVER_COL: col_width(ST_COVER_COL, "100%", mono_fm),
            ST_DIST_COL: col_width(ST_DIST_COL, "9,999.9", mono_fm),
            ST_ARRIVAL_COL: col_width(ST_ARRIVAL_COL, "9,999.9k", mono_fm),
        }
        # Floors the shared name columns collapse to: the header word plus a
        # breath of space on each side, so the label itself never clips.
        # System is the first to yield down to this floor; the station name
        # follows the same rule once system is there.
        system_min = head_fm.horizontalAdvance("System") + 2 * pad
        station_min = head_fm.horizontalAdvance("Station") + 2 * pad
        for table in (self.stations_table, self.stations_table2):
            table.fixed_widths = widths
            table.system_min = system_min
            table.station_min = station_min
            table.relayout_columns()

    def _sync_min_width(self) -> None:
        """Pin both pages to one minimum width so every tab bottoms out alike.

        Hidden widgets don't count towards a layout's minimum, so the two
        pages would otherwise each impose their own floor and the window's
        minimum width would change with the selected tab.
        """
        floor = max(
            self.commodity_page.layout().totalMinimumSize().width(),
            self.stations_page.layout().totalMinimumSize().width(),
        )
        self.commodity_page.setMinimumWidth(floor)
        self.stations_page.setMinimumWidth(floor)

    def _tool(self, text: str, tip: str, checkable: bool = False) -> QToolButton:
        b = QToolButton()
        b.setText(text)
        b.setToolTip(tip)
        b.setCheckable(checkable)
        b.setCursor(Qt.PointingHandCursor)
        return b

    def _window_control(
        self, text: str, tip: str, checkable: bool = False
    ) -> QToolButton:
        """Build one of the four compact controls in the title row."""
        button = self._tool(text, tip, checkable)
        theme.configure_window_control(button)
        return button

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
        self._apply_station_column_widths()
        self._sync_min_width()

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
        # Re-pin after rendering: widgets appearing/disappearing (e.g. the
        # complete-construction button) shift the pages' natural minimums.
        self._sync_min_width()
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

        self.model.set_station_stock(state.docked_station_stock())
        self._current_rows = proj.rows(state.cargo, state.carrier_cargo)
        self.model.set_rows(self._current_rows)

        delivered = proj.total_provided()
        required = proj.total_required()
        remaining = required - delivered
        self.totals_label.setText(
            f"{delivered:,} / {required:,} t delivered · {remaining:,} t to go"
        )
        # Offer to clear the finished site (real projects only: the combined
        # view aggregates several sites and can't be "completed" as one).
        self.complete_btn.setVisible(
            proj.market_id != COMBINED_MARKET_ID
            and (proj.complete or proj.all_delivered)
        )
        self._update_carrier_label(state)

    def _complete_construction(self) -> None:
        """Remove the finished construction currently in view (and its tab)."""
        if self._state is None:
            return
        mid = self._selected_market() if self.tabs.isVisible() else None
        if mid is None:  # tab bar hidden: the single remaining project is shown
            projects = self._state.project_list()
            mid = projects[0].market_id if projects else None
        if mid is None or mid not in self._state.projects:
            return
        self._state.remove_project(mid)
        self.project_removed.emit()
        self.refresh(self._state)

    def _render_empty(self) -> None:
        self.title_label.setText("No construction projects yet")
        self.subtitle_label.setText("Dock at a colonisation construction site")
        self.progress.setValue(0)
        self.percent_label.setText("0%")
        self._current_rows = []
        self.model.set_rows([])
        self.totals_label.setText("")
        self.complete_btn.setVisible(False)
        self._update_carrier_label(self._state)

    #  nearest stations -

    def _show_page(self, *, stations: bool) -> None:
        """Show either the station-search page or the commodity page."""
        self.stations_page.setVisible(stations)
        self.commodity_page.setVisible(not stations)

    def _render_stations(self, state: AppState) -> None:
        """Switch to the station-search page; the first view triggers the search."""
        self._show_page(stations=True)
        self.title_label.setText("Nearest stations")
        system = state.current_system or "unknown"
        self.subtitle_label.setText(f"Buying near {system}")
        needs = state.outstanding_needs()
        ref_text = f"Near {system} · {len(needs)} commodities needed"
        if self.recent_btn.isChecked():
            ref_text += " · markets ≤24h old"
        # Results are anchored to where the search ran; after a jump they're
        # still shown, just flagged so the user knows ↻ re-anchors them.
        if self._search_ref and self._search_ref != state.current_system:
            ref_text += f" · results from {self._search_ref} (↻ to update)"
        elif self._search_needs and self._search_needs != needs:
            ref_text += " · demand changed (↻ to update)"
        self.stations_ref_label.setText(ref_text)

        if not state.current_system:
            self.stations_status.setText(
                "Waiting for your location - jump or dock so EDSC knows where you are."
            )
            self.stations_model.set_rows([])
            self._clear_follow_up_stations()
            return
        if not needs:
            self.stations_status.setText("Nothing outstanding - all commodities covered.")
            self.stations_model.set_rows([])
            self._clear_follow_up_stations()
            return

        # Auto-search only once per session; afterwards Spansh is hit again
        # solely by ↻ Search or the Recent pre-search toggle.
        if not self._searching and not self._stations_searched:
            self._start_station_search(state)
        elif not self._searching and self._station_results:
            self._apply_cached_results()

    def _clear_follow_up_stations(self) -> None:
        """Empty and hide the complementary 'fill the rest at' table."""
        self.stations_model2.set_rows([])
        self.stations_more_bar.setVisible(False)
        self.stations_table2.setVisible(False)

    def _refresh_stations(self) -> None:
        """Manual refresh button: force a fresh search."""
        if self._state is not None:
            self._start_station_search(self._state)

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
        self._refresh_stations()

    def _toggle_follow_up_planets(self, _checked: bool) -> None:
        """Apply the supplementary surface filter to cached candidates."""
        self._apply_cached_results()

    def _toggle_follow_up_carriers(self, _checked: bool) -> None:
        """Apply the supplementary carrier filter to cached candidates."""
        self._apply_cached_results()

    def _set_search_controls_enabled(self, enabled: bool) -> None:
        for button in (
            self.planets_btn,
            self.carriers_btn,
            self.recent_btn,
            self.refresh_btn,
            self.planets_btn2,
            self.carriers_btn2,
        ):
            button.setEnabled(enabled)

    def _copy_station_system(self, index: QModelIndex) -> None:
        """Clicking a System cell copies the name, ready to paste in-game."""
        if index.column() != ST_SYSTEM_COL:
            return
        table = self.sender()
        model = table.model() if table is not None else self.stations_model
        result = model.row_at(index.row())
        if result is None:
            return
        QGuiApplication.clipboard().setText(result.system)
        previous = self.stations_status.text()
        notice = f"Copied '{result.system}' to clipboard"
        self.stations_status.setText(notice)

        def restore() -> None:
            # Skip if a search or another copy replaced the notice meanwhile.
            if self.stations_status.text() == notice:
                self.stations_status.setText(previous)

        QTimer.singleShot(2500, restore)

    def _start_station_search(self, state: AppState) -> None:
        reference = state.current_system
        needs = dict(state.outstanding_needs())
        if not reference or not needs:
            return
        self._searching = True
        self._stations_searched = True
        self._search_seq += 1
        seq = self._search_seq
        recent_only = self.recent_btn.isChecked()
        qualifier = " recent" if recent_only else ""
        self.stations_status.setText(
            f"Searching Spansh near {reference} for 10{qualifier} orbital, "
            f"10 planetary, and 10 carrier markets…"
        )
        self._set_search_controls_enabled(False)
        # Pass the amounts too: a station only counts as stocking a commodity
        # when its supply covers (a useful chunk of) the shortfall.
        task = _SearchTask(reference, needs, recent_only)
        # Keep a Python reference so the task and its signal object aren't
        # garbage-collected before the queued result reaches the GUI thread.
        task.setAutoDelete(False)
        self._search_task = task
        task.signals.done.connect(
            lambda results, s=seq, ref=reference, demand=needs, recent=recent_only:
            self._on_search_done(s, ref, demand, recent, results)
        )
        task.signals.error.connect(
            lambda message, s=seq: self._on_search_error(s, message)
        )
        self._search_pool.start(task)

    def _on_search_done(
        self,
        seq: int,
        reference: str,
        needed: dict[str, int],
        recent_only: bool,
        results: list,
    ) -> None:
        if seq != self._search_seq:
            return  # a newer search superseded this one
        self._searching = False
        self._search_task = None
        self._set_search_controls_enabled(True)
        self._search_ref = reference
        self._search_needs = dict(needed)
        self._search_recent_only = recent_only
        self._station_results = list(results)

        if not self._station_results:
            self.stations_model.set_rows([])
            self._clear_follow_up_stations()
            self.stations_status.setText(
                "No stations found selling those commodities nearby."
            )
            self._fit_height()
            return
        self._apply_cached_results()

    def _apply_cached_results(self) -> None:
        """Rebuild both station tables from the one fetched pool, without I/O."""
        if not self._station_results or not self._search_needs:
            return
        primary_results = station_search.filter_stations(
            self._station_results,
            include_planetary=self.config.stations_include_planets,
            include_carriers=self.config.stations_include_carriers,
        )
        self.stations_model.set_rows(
            station_search.limit_mixed_results(primary_results, _RESULTS_SHOWN)
        )
        primary = self.stations_model.row_at(0)
        if primary is None:
            self._clear_follow_up_stations()
            self.stations_status.setText(
                "No cached stations match the primary filters - enable Planets "
                "or Carriers, or press ↻ Search."
            )
            self._fit_height()
            return

        residual = station_search.residual_demand(self._search_needs, primary)
        secondary_pool = station_search.filter_stations(
            self._station_results,
            include_planetary=self.planets_btn2.isChecked(),
            include_carriers=self.carriers_btn2.isChecked(),
        )
        follow_up = station_search.limit_mixed_results(
            station_search.supplementary_candidates(
                secondary_pool,
                self._search_needs,
                primary,
            ),
            _RESULTS_SHOWN,
        )
        self._show_follow_up_results(follow_up, residual)

        # Completeness is calculated with a greedy stop plan independently of
        # the alternatives displayed above. One planetary station completing
        # the plan must not suppress useful carrier alternatives in the table.
        filtered_plan = station_search.supplementary_stations(
            secondary_pool,
            self._search_needs,
            primary,
            limit=_RESULTS_SHOWN,
        )
        filtered_remaining = station_search.remaining_demand(
            self._search_needs, [primary, *filtered_plan]
        )
        complete_follow_up = station_search.supplementary_stations(
            self._station_results,
            self._search_needs,
            primary,
            limit=_RESULTS_SHOWN,
        )
        pool_remaining = station_search.remaining_demand(
            self._search_needs, [primary, *complete_follow_up]
        )
        orbital, planetary, carriers = self._category_counts(self._station_results)
        text = (
            f"Cached {orbital} orbital · {planetary} planetary · {carriers} carriers"
            f" · best {primary.match_count}/{primary.needed_total} "
            f"({primary.coverage * 100:.0f}%) at {primary.distance_ly:,.1f} ly"
        )
        if pool_remaining:
            text += f" · {self._shortfall_text(pool_remaining)} not found in fetched data"
        elif filtered_remaining:
            text += (
                f" · {self._shortfall_text(filtered_remaining)} hidden by the "
                "supplementary filters"
            )
        elif residual:
            text += f" · complete in {1 + len(filtered_plan)} stops"
        else:
            text += " · complete in one stop"
        if self._search_recent_only:
            text += " · ≤24h old"
        self.stations_status.setText(text)
        self._fit_height()

    @staticmethod
    def _category_counts(results: list) -> tuple[int, int, int]:
        orbital = sum(1 for station in results if not station.is_planetary and not station.is_carrier)
        planetary = sum(1 for station in results if station.is_planetary and not station.is_carrier)
        carriers = sum(1 for station in results if station.is_carrier)
        return orbital, planetary, carriers

    @staticmethod
    def _shortfall_text(remaining: dict[str, int]) -> str:
        names = sorted(remaining)
        text = ", ".join(names[:3])
        if len(names) > 3:
            text += f" +{len(names) - 3} more"
        return text

    def _show_follow_up_results(self, results: list, residual: dict) -> None:
        """Display a locally planned supplementary stop list.

        The section remains visible after an empty result so its independent
        filters are available for broadening the cached candidates.
        """
        self.stations_model2.set_rows(results[:_RESULTS_SHOWN])
        has_residual = bool(residual)
        self.stations_more_label.setText(
            "Fill the rest at:"
            if results
            else "No cached stations match the remaining demand:"
        )
        self.stations_more_bar.setVisible(has_residual)
        self.stations_table2.setVisible(has_residual)

    def _on_search_error(self, seq: int, message: str) -> None:
        if seq != self._search_seq:
            return
        self._searching = False
        self._search_task = None
        self._set_search_controls_enabled(True)
        if not self._station_results:
            self.stations_model.set_rows([])
            self._clear_follow_up_stations()
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
                f" - exceeds the carrier's {state.carrier_total:,} t;"
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
        table.cap = max(
            theme.METRICS.auto_height_minimum,
            int(avail * theme.METRICS.auto_height_screen_fraction)
            - theme.METRICS.auto_height_chrome,
        )

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
        # A station search may still be blocked on the network; make sure it
        # won't emit into widgets that are about to be destroyed.
        if self._search_task is not None:
            self._search_task.cancel()
        self._search_pool.clear()  # drop any queued (not yet started) searches

    def closeEvent(self, event) -> None:
        # Window-manager close (e.g. Alt+F4) means quit; the header "-" button
        # only hides to tray instead.
        self.persist_geometry()
        self.stop()
        self.quit_requested.emit()
        event.accept()
