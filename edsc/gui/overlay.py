"""The in-game overlay window."""

# SPDX-License-Identifier: GPL-3.0-or-later

from __future__ import annotations

from PySide6.QtCore import QEvent, QPoint, Qt, QThreadPool, QTimer, Signal
from PySide6.QtGui import QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QMenu,
    QSizeGrip,
    QTabBar,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from .. import __version__
from ..config import Config
from ..model import (
    COLONIZE_MARKET_ID,
    COMBINED_MARKET_ID,
    STATIONS_MARKET_ID,
    AppState,
    Project,
)
from ..platform import clickthrough, foreground
from . import theme
from .collapse_controller import CollapseController
from .collapse_icon import CollapsedIcon
from .colonize_page import ColonizePage
from .commodity_page import CommodityPage
from .stations_page import StationsPage
from .widgets import DragBar, ElideLabel, tool_button

# How often to check which window is focused (ms).
_FOCUS_POLL_MS = 250


class OverlayWindow(QWidget):
    """The overlay shell: header, tab bar, and the page currently in view; owns the window (flags, geometry, click-through, auto-height) and routes app state to one page per tab (see ``CommodityPage``/``StationsPage``/``ColonizePage``), search pages also exposing ``busy``/``refresh()`` and sharing one single-threaded pool so all Spansh work stays serialized."""

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
        self._click_through_active = False
        self._game_focused = False
        # Session flag: cleared when the user grabs the resize grip, so a manually chosen height isn't snapped back by the next auto-fit; re-applying Settings re-arms it.
        self._auto_fit = True
        self._search_pool = QThreadPool(self)
        self._search_pool.setMaxThreadCount(1)  # one search at a time

        self.setWindowTitle("EDSC")
        self.setAttribute(Qt.WA_TranslucentBackground)
        self._apply_window_flags()

        self.panel = QFrame(self)
        theme.set_role(self.panel, theme.PANEL_ROLE)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(*theme.METRICS.overlay_outer_margins)
        outer.addWidget(self.panel)

        # Keep the panel background separate from its contents so the collapse can fade the readable UI without ever squeezing its text.
        self.content = QWidget(self.panel)
        panel_layout = QVBoxLayout(self.panel)
        panel_layout.setContentsMargins(0, 0, 0, 0)
        panel_layout.setSpacing(0)
        panel_layout.addWidget(self.content)

        self.collapse_icon = CollapsedIcon(
            config, on_restore=lambda: self.set_collapsed(False)
        )
        self._collapse = CollapseController(
            self,
            self.panel,
            self.content,
            self.collapse_icon,
            config,
            game_focused=lambda: self._game_focused,
        )

        self._build_ui()
        self.apply_appearance()
        self.resize(config.overlay_width, config.overlay_height)
        self.move(config.overlay_x, config.overlay_y)
        self._collapse.note_geometry()

        # Focus-driven click-through: poll which window is foreground and, while the game is focused, let the mouse pass through to it.
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

        # Tab switching; fires while the overlay is focused, a global X11 hotkey (installed by the app) covers switching while in-game.
        for seq, slot in (
            ("Ctrl+Shift+Left", self.select_prev_tab),
            ("Ctrl+Shift+Right", self.select_next_tab),
            ("Ctrl+Shift+Down", self.toggle_collapsed),
        ):
            sc = QShortcut(QKeySequence(seq), self)
            sc.setContext(Qt.ApplicationShortcut)
            sc.activated.connect(slot)

    #  construction

    def _build_ui(self) -> None:
        root = QVBoxLayout(self.content)
        root.setContentsMargins(*theme.METRICS.panel_margins)
        root.setSpacing(theme.METRICS.content_spacing)

        root.addWidget(self._build_header())

        # One tab per construction, plus a combined "All" tab; visible only when there are at least two constructions to switch between.
        self.tabs = QTabBar()
        self.tabs.setExpanding(False)
        self.tabs.setDrawBase(False)
        self.tabs.setElideMode(Qt.ElideRight)
        self.tabs.setUsesScrollButtons(True)
        self.tabs.currentChanged.connect(self._on_tab_changed)
        # Right-click a project tab to remove it (finished/abandoned sites otherwise accumulate forever; docking there again re-adds it).
        self.tabs.setContextMenuPolicy(Qt.CustomContextMenu)
        self.tabs.customContextMenuRequested.connect(self._tab_context_menu)
        root.addWidget(self.tabs)

        self._build_pages(root)

        # Shared bottom: the status line, then a credit footer with the grip.
        self.status_label = QLabel("Starting…")
        theme.set_role(self.status_label, theme.STATUS_ROLE)
        self.status_label.setWordWrap(True)
        root.addWidget(self.status_label)

        credit_row = QHBoxLayout()
        self.credit_label = QLabel(f"EDSC {__version__} · by CMDR FEEDMEWEED 2026")
        theme.set_role(self.credit_label, theme.CREDIT_ROLE)
        credit_row.addWidget(self.credit_label, 1)
        # Grabbing the grip means the user wants this size: stop auto-fitting for the session (the event filter clears the flag on press).
        self._size_grip = QSizeGrip(self)
        self._size_grip.installEventFilter(self)
        credit_row.addWidget(self._size_grip, 0, Qt.AlignBottom | Qt.AlignRight)
        root.addLayout(credit_row)

    def _build_header(self) -> QWidget:
        """Title + window buttons, draggable."""
        self.header = DragBar(self, on_release=self.persist_geometry)
        header_row = QHBoxLayout(self.header)
        header_row.setContentsMargins(*theme.METRICS.page_margins)
        self.project_icon_label = QLabel()
        theme.set_role(self.project_icon_label, theme.IMAGE_ROLE)
        self.project_icon_label.setFixedSize(32, 32)
        self.project_icon_label.setAlignment(Qt.AlignCenter)
        self.project_icon_label.hide()
        header_row.addWidget(self.project_icon_label, 0, Qt.AlignVCenter)

        titles = QVBoxLayout()
        titles.setSpacing(theme.METRICS.header_spacing)
        # Elided: a long construction-site name must not dictate how narrow the window can go (the minimum must not depend on tab content).
        self.title_label = ElideLabel()
        theme.set_role(self.title_label, theme.TITLE_ROLE)
        self.title_label.setText("EDSC - Supply Chain")
        self.subtitle_label = ElideLabel()
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
        self.collapse_btn = self._window_control(
            "▣", "Collapse to a floating icon (Ctrl+Shift+Down)"
        )
        self.collapse_btn.clicked.connect(lambda: self.set_collapsed(True))
        self.hide_btn = self._window_control("-", "Hide to tray")
        self.hide_btn.clicked.connect(self.hide)
        for b in (
            self.pin_btn,
            self.ghost_btn,
            self.settings_btn,
            self.collapse_btn,
            self.hide_btn,
        ):
            header_row.addWidget(b)
        return self.header

    def _build_pages(self, root: QVBoxLayout) -> None:
        """Build the interchangeable pages and register the search tabs; they share one slot (only one visible) so the window auto-fits its height to the showing list, with stretch 1 letting the visible page absorb extra height while header/tabs stay pinned top and footers bottom."""
        self.commodity_page = CommodityPage(self.config)
        self.commodity_page.carrier_edited.connect(self._on_carrier_edited)
        self.commodity_page.complete_requested.connect(self._remove_project)
        self.commodity_page.rerender_requested.connect(self._rerender)

        self.stations_page = StationsPage(self.config, self._search_pool)
        self.colonize_page = ColonizePage(self.config, self._search_pool)

        # Market id -> the page that owns that tab; the commodity page isn't here, it serves every project tab keyed by the project itself.
        self._search_pages = {
            STATIONS_MARKET_ID: self.stations_page,
            COLONIZE_MARKET_ID: self.colonize_page,
        }
        self._all_pages = (self.commodity_page, self.stations_page, self.colonize_page)
        self._current_page: QWidget = self.commodity_page

        for page in self._all_pages:
            root.addWidget(page, 1)
            page.setVisible(page is self._current_page)
        for page in self._search_pages.values():
            page.content_changed.connect(self._fit_height)

    def _window_control(
        self, text: str, tip: str, checkable: bool = False
    ) -> QToolButton:
        """Build one of the compact controls in the title row."""
        button = tool_button(text, tip, checkable)
        theme.configure_window_control(button)
        return button

    def _sync_min_width(self) -> None:
        """Pin all pages to one minimum width so every tab bottoms out alike; hidden widgets don't count towards a layout's minimum, so otherwise each page would impose its own floor and the window's minimum width would change with the selected tab."""
        floor = max(
            page.layout().totalMinimumSize().width() for page in self._all_pages
        )
        for page in self._all_pages:
            page.setMinimumWidth(floor)

    #  appearance / flags

    def _apply_window_flags(self) -> None:
        flags = Qt.FramelessWindowHint | Qt.Tool
        if self.config.always_on_top:
            # Keeps the overlay above normal + borderless-fullscreen windows. NOTE: no external window can cover *exclusive* fullscreen (the compositor unredirects it); users must run ED in Borderless (see the README "Display mode" section).
            flags |= Qt.WindowStaysOnTopHint
        self.setWindowFlags(flags)

    def apply_appearance(self) -> None:
        for page in self._all_pages:
            page.apply_appearance()
        self.set_opacity(self.config.overlay_opacity)
        self._sync_min_width()
        self.collapse_icon.apply_appearance()
        self._refresh_header_icon()

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
        self.collapse_icon.apply_flags()

        self.ghost_btn.blockSignals(True)
        self.ghost_btn.setChecked(
            self.config.auto_click_through and self._detector.available
        )
        self.ghost_btn.blockSignals(False)
        self._update_focus_state()

        for page in self._all_pages:
            page.sync_from_config()

        self._auto_fit = True  # applying Settings re-arms height auto-fit
        if self._state is not None:
            self._fit_height()

    #  external API

    def set_opacity(self, opacity: float) -> None:
        """Apply a transient panel opacity without changing saved settings."""
        opacity = max(0.0, min(1.0, opacity))
        self.panel.setStyleSheet(
            theme.panel_stylesheet(
                alpha=int(opacity * 255),
                font_pt=self.config.font_point_size,
            )
        )

    def set_status(self, text: str) -> None:
        self.status_label.setText(text)

    @property
    def focus_detection_available(self) -> bool:
        """Whether game-window focus can be detected in this session."""
        return self._detector.available

    def refresh(self, state: AppState) -> None:
        """Re-render from the current app state (called on every change)."""
        self._state = state
        projects = state.project_list()
        ids = [p.market_id for p in projects]

        if ids != self._project_ids:
            self._rebuild_tabs(projects)
            self._project_ids = ids

        self._render_current(state)
        # Re-pin after rendering: widgets appearing/disappearing (e.g. the complete-construction button) shift the pages' natural minimums.
        self._sync_min_width()
        self._fit_height()

    def refresh_current_search(self) -> None:
        """Refresh the selected search tab, ignoring non-search and busy tabs."""
        page = self._search_pages.get(self._selected_market())
        if page is not None and not page.busy:
            page.refresh()

    #  collapse to icon

    @property
    def collapsed(self) -> bool:
        """Whether the overlay is currently collapsed into the floating icon."""
        return self._collapse.collapsed

    def set_collapsed(self, collapsed: bool) -> None:
        self._collapse.set_collapsed(collapsed)

    def toggle_collapsed(self) -> None:
        self._collapse.toggle()

    #  tabs

    def _rebuild_tabs(self, projects: list[Project]) -> None:
        """One tab per construction, prefixed with a combined 'All' tab; the 'All' tab is added once there are two or more (with one it would just duplicate it), each tab storing its market id (or sentinel) as tab data. Project and Stations tabs need a construction, but Colonize has no prerequisite so the bar is always visible -- a commander who hasn't started a colony is exactly who browses colonization targets."""
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
        cidx = self.tabs.addTab("Colonize")
        self.tabs.setTabData(cidx, COLONIZE_MARKET_ID)
        self.tabs.setTabToolTip(cidx, "Nearby unclaimed systems you could colonise")
        self._select_market(self.config.selected_market_id)

        self.tabs.setVisible(True)
        self.tabs.blockSignals(False)

    def _tab_label(self, project: Project) -> str:
        name = project.station_name or project.title
        for prefix in (
            "Orbital Construction Site: ",
            "Planetary Construction Site: ",
            "Construction Site: ",
        ):
            if name.startswith(prefix):
                name = name[len(prefix) :]
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
        if mid in (COMBINED_MARKET_ID, STATIONS_MARKET_ID, COLONIZE_MARKET_ID):
            return
        proj = self._state.projects.get(mid)
        if proj is None:
            return
        menu = QMenu(self)
        remove = menu.addAction(f"Remove “{proj.title}”")
        if menu.exec(self.tabs.mapToGlobal(pos)) is remove:
            self._remove_project(mid)

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

    #  page routing

    def _show_page(self, page: QWidget) -> None:
        """Show exactly one page."""
        self._current_page = page
        for candidate in self._all_pages:
            candidate.setVisible(candidate is page)

    def _apply_header(self, page: QWidget) -> None:
        """Adopt the header the page just rendered for itself."""
        icon = page.header_icon()
        if icon is None:
            self.project_icon_label.hide()
        else:
            self.project_icon_label.setPixmap(icon)
            self.project_icon_label.show()
        self.title_label.setText(page.title)
        self.subtitle_label.setText(page.subtitle)

    def _refresh_header_icon(self) -> None:
        """Re-render the header emblem so it picks up HUD recolours."""
        if self.project_icon_label.isHidden():
            return
        icon = self._current_page.header_icon()
        if icon is not None:
            self.project_icon_label.setPixmap(icon)

    def _render_current(self, state: AppState) -> None:
        mid = self._selected_market() if self.tabs.isVisible() else None
        page = self._search_pages.get(mid)
        if page is not None:
            self._show_page(page)
            page.render(state)
            self._apply_header(page)
            return

        self._show_page(self.commodity_page)
        if mid == COMBINED_MARKET_ID:
            active = state.active_projects()
            self.commodity_page.render(
                state.combined_project(), state, subtitle=f"{len(active)} constructions"
            )
        elif mid is not None and mid in state.projects:
            self.commodity_page.render(state.projects[mid], state)
        else:
            # No tab to route by (the bar isn't up yet, e.g. before first show): fall back to the single project, or the placeholder.
            projects = state.project_list()
            if projects:
                self.commodity_page.render(projects[0], state)
            else:
                self.commodity_page.render_empty(state)
        self._apply_header(self.commodity_page)

    #  project actions

    def _remove_project(self, market_id) -> None:
        """Drop a construction (and its tab) at the user's request."""
        if self._state is None or market_id not in self._state.projects:
            return
        self._state.remove_project(market_id)
        self.project_removed.emit()
        self.refresh(self._state)

    def _on_carrier_edited(self) -> None:
        self.carrier_changed.emit()
        self._rerender()

    def _rerender(self) -> None:
        if self._state is not None:
            self.refresh(self._state)

    #  toggles

    def _toggle_pin(self, checked: bool) -> None:
        self.config.always_on_top = checked
        pos = self.pos()
        self._apply_window_flags()
        self.move(pos)
        self.show()  # re-applying flags requires a re-show
        self.collapse_icon.apply_flags()

    def _toggle_auto_click_through(self, checked: bool) -> None:
        self.config.auto_click_through = checked
        if checked:
            self._update_focus_state()  # apply immediately
        else:
            self._set_click_through(False)  # always interactive / movable

    def _update_focus_state(self) -> None:
        """Timer slot: track game focus; click-through only while it's focused. Game focus is computed regardless of the click-through toggle because other consumers (global hotkey grabs) key off it too."""
        game_focused = False
        if self._detector.available:
            info = self._detector.active()
            game_focused = info is not None and info.matches(
                self.config.game_window_matchers
            )
        if game_focused != self._game_focused:
            self.game_focus_changed.emit(game_focused)
        self._set_click_through(game_focused and self.config.auto_click_through)
        # When the game grabs focus a window manager may restack it over us; re-assert keep-above so the overlay stays over a (borderless) game window. Only on the rising edge to avoid fighting the WM.
        if game_focused and not self._game_focused and self.config.always_on_top:
            self._collapse.assert_topmost()
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
        if (
            not self.config.auto_height
            or not self._auto_fit
            or self._collapse.transition_active
        ):
            return
        table = self._current_page.fit_table
        # Cap growth so a huge list scrolls instead of leaving the screen.
        screen = self.screen()
        avail = screen.availableGeometry().height() if screen else 1000
        # Leave room for the non-table chrome (header, progress, footer...).
        table.cap = max(
            theme.METRICS.auto_height_minimum,
            int(avail * theme.METRICS.auto_height_screen_fraction)
            - theme.METRICS.auto_height_chrome,
        )

        # Activate the nested layouts so the top-level size hint reflects the new row count immediately (without waiting for the event loop to relayout).
        table.updateGeometry()
        self.content.layout().activate()
        self.panel.layout().activate()
        self.layout().activate()

        target = self.sizeHint().height()
        if self.height() != target:
            self.resize(self.width(), target)

    #  geometry persistence

    def persist_geometry(self) -> None:
        self._collapse.persist_geometry()

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self.persist_geometry()

    def stop(self) -> None:
        """Release focus-watching resources (called on shutdown)."""
        self._focus_timer.stop()
        self._detector.close()
        self._collapse.stop()
        for page in self._search_pages.values():
            page.stop()
        self._search_pool.clear()  # drop any queued (not yet started) searches

    def closeEvent(self, event) -> None:
        # Window-manager close (e.g. Alt+F4) means quit; the header "-" button only hides to tray instead.
        self.persist_geometry()
        self.stop()
        self.quit_requested.emit()
        event.accept()
