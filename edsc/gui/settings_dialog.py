"""Tabbed settings dialog for application, search, sharing, and input."""

# SPDX-License-Identifier: GPL-3.0-or-later

from __future__ import annotations

import uuid
from collections.abc import Callable
from typing import TYPE_CHECKING

from PySide6.QtCore import QSize, Qt, QTimer, Signal
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPlainTextEdit,
    QPushButton,
    QSlider,
    QSpinBox,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from .. import __version__
from ..config import COLONIZE_RANGE_MAX, COLONIZE_RANGE_MIN, Config
from ..eddn import format_activity
from ..journal import locator
from . import gizmo, icons, theme
from .about_easter_egg import AboutEasterEggController, ClickableImage
from .controller_tester import ControllerTesterWidget, development_mode_enabled
from .widgets import DragBar as _DragBar

if TYPE_CHECKING:
    from ..eddn import EddnUplink
    from ..platform.controller import ControllerMonitor

# How often the EDDN activity console repaints while the dialog is open.
_EDDN_CONSOLE_REFRESH_MS = 1000

CONTACT_EMAIL = "dev@thepineapple.express"
PROJECT_URL = "https://github.com/ThePineappleExpress/edsc"
APP_FULL_NAME = "Elite Dangerous Supply Chain"

# Construction (station) search radius slider; the far-right position is a discrete "Unlimited" end-stop restoring the historical no-cap search.
CONSTR_RANGE_MIN = 20
CONSTR_RANGE_MAX = 500
CONSTR_RANGE_STEP = 20
CONSTR_RANGE_UNLIMITED = CONSTR_RANGE_MAX + CONSTR_RANGE_STEP

# Flight gizmo size as a % of the 200px base: small enough to tuck in a corner, large enough to read from across a cockpit.
_GIZMO_SCALE_MIN = 50
_GIZMO_SCALE_MAX = 250

# Colonization body-richness weight slider maps 0..30 to a 0.0..3.0 weight.
COLONIZE_WEIGHT_SCALE = 10
COLONIZE_WEIGHT_MAX = 30

# (label, stored value) pairs for the sort dropdowns.
CONSTRUCTION_SORTS = (
    ("Best match", "match"),
    ("Nearest", "nearest"),
    ("Freshest market", "fresh"),
)
COLONIZE_SORTS = (
    ("Balanced", "balanced"),
    ("Nearest", "nearest"),
    ("Most bodies", "bodies"),
)

# Community services credited on the About tab: (asset stem, caption); their 64px marks are HUD-tinted at runtime (see icons.powered_logo_pixmap).
_POWERED_BY = (
    ("eddn", "EDDN"),
    ("spansh", "Spansh"),
    ("raven", "Raven Colonial"),
)

# Attribution / disclaimer shown beneath the emblem on the About tab.
_ATTRIBUTION = (
    "A free, community-built companion — not affiliated with Frontier "
    "Developments. Built on open source: Python and Qt (PySide6), plus the "
    "community data networks above. Ship boost data from coriolis-data (MIT) "
    "and the boost formula from EDSY. Released under the GPLv3 with NO WARRANTY "
    "and no guarantee of fitness — use at your own risk and always double-check "
    "before a long haul. Made by CMDRs, for CMDRs. o7"
)

# Visual tips shown on the "?" tab as (glyph, title, one-line body) cards.
_GETTING_STARTED = (
    ("⬢", "Dock to track", "Dock at a construction site to open its tab — Need, "
     "Hold, Carrier, Done and Short update live as you deliver."),
    ("⇄", "Switch projects", "Tabs or Ctrl+Shift+←/→ swap projects (in-game "
     "too); the All tab sums every project's outstanding needs."),
    ("⚑", "Find markets", "The ⚑ Stations tab finds the nearest large-pad "
     "markets selling what you're still short of."),
    ("✧", "Scout systems", "The Colonize tab ranks nearby unclaimed systems "
     "worth settling."),
    ("▣", "Collapse & move", "▣ or Ctrl+Shift+↓ shrinks to the floating icon; "
     "drag the header to move, the corner grip to resize."),
)
_GOOD_TO_KNOW = (
    ("▢", "Run borderless", "The overlay can't draw over exclusive fullscreen — "
     "use Borderless (on KDE, a one-time KWin window rule)."),
    ("⚙", "Steam Deck", "Game Mode (gamescope) hides external overlays — play in "
     "Desktop Mode to keep EDSC visible."),
    ("⧉", "Carrier cargo", "Elite doesn't expose carrier inventory, so the "
     "Carrier column is estimated from transfers — correct it with FC…."),
    ("◷", "Check freshness", "Market data is community-sourced and can be stale "
     "— check a row's market-age tooltip before a long haul."),
    ("⬗", "Linux / Wayland", "EDSC runs on XWayland so focus tracking, "
     "click-through and always-on-top work alongside the game."),
)


class _FitTabs(QTabWidget):
    """A tab widget whose size hint tracks the *current* page; a stock ``QTabWidget`` reports the tallest page's hint regardless of which tab shows, so reporting the visible page's hint lets the window shrink/grow to the active tab and nothing scrolls."""

    # A couple of pixels of slack for the pane frame around the page.
    _PANE_SLACK = 4

    def _hint_for_current(self, base: QSize, minimum: bool) -> QSize:
        page = self.currentWidget()
        if page is None:
            return base
        page_h = (
            page.minimumSizeHint().height() if minimum else page.sizeHint().height()
        )
        bar_h = self.tabBar().sizeHint().height()
        return QSize(base.width(), bar_h + page_h + self._PANE_SLACK)

    def sizeHint(self) -> QSize:
        return self._hint_for_current(super().sizeHint(), minimum=False)

    def minimumSizeHint(self) -> QSize:
        return self._hint_for_current(super().minimumSizeHint(), minimum=True)


class SettingsDialog(QDialog):
    # Fires when the gizmo aim crosshairs should show/hide: true while the Controls tab is open with gizmos enabled, false otherwise.
    gizmo_targeting_changed = Signal(bool)

    def __init__(
        self,
        config: Config,
        parent: QWidget | None = None,
        *,
        eddn_status: str | None = None,
        eddn_uplink: EddnUplink | None = None,
        on_eddn_sync: Callable[[], tuple[bool, bool] | None] | None = None,
        controllers: ControllerMonitor | None = None,
        development_mode: bool | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("EDSC Settings")
        self.config = config
        self._eddn_status = eddn_status
        self._eddn_uplink = eddn_uplink
        self._on_eddn_sync = on_eddn_sync
        self._controllers = controllers
        self._development_mode = (
            development_mode_enabled()
            if development_mode is None
            else bool(development_mode)
        )
        self.setMinimumWidth(theme.METRICS.settings_dialog_minimum_width)

        # Overlay-style frameless shell: a translucent margin around a themed panel, so the dialog matches the HUD not the desktop.
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setWindowFlags(Qt.Dialog | Qt.FramelessWindowHint)
        self.panel = QFrame(self)
        theme.set_role(self.panel, theme.PANEL_ROLE)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(*theme.METRICS.overlay_outer_margins)
        outer.addWidget(self.panel)

        self._root = QVBoxLayout(self.panel)
        root = self._root
        root.setContentsMargins(*theme.METRICS.panel_margins)
        root.setSpacing(theme.METRICS.content_spacing)
        self._title_bar = self._build_title_bar()
        root.addWidget(self._title_bar)

        self.tabs = _FitTabs()
        self.tabs.addTab(self._build_general_tab(), "General")
        self.tabs.addTab(self._build_display_tab(), "Display")
        self.tabs.addTab(self._build_construction_tab(), "Construction")
        self.tabs.addTab(self._build_colonization_tab(), "Colonization")
        self.tabs.addTab(self._build_eddn_tab(), "EDDN")
        self._controls_index = self.tabs.addTab(
            self._build_controls_tab(), "Controls"
        )
        self._about_index = self.tabs.addTab(self._build_about_tab(), "About")
        self.tabs.addTab(self._build_help_tab(), "?")
        # Tab pages differ a lot in height (About carries the full emblem), so the window tracks the active tab rather than the tallest or scrolling (hidden pages report no height preference).
        self.tabs.currentChanged.connect(self._fit_to_current_tab)
        root.addWidget(self.tabs)

        # The aim crosshairs are editable only from the Controls tab and only once gizmos are on; watch both so they appear and vanish in step.
        self._gizmo_targeting = False
        self.tabs.currentChanged.connect(self._sync_gizmo_targeting)
        self.gizmo_enabled.toggled.connect(self._sync_gizmo_targeting)

        self._buttons = QDialogButtonBox(
            QDialogButtonBox.Ok | QDialogButtonBox.Cancel
        )
        self._buttons.accepted.connect(self.accept)
        self._buttons.rejected.connect(self.reject)
        # Drop the style's stock button glyphs (Cancel ✕ / OK ✓): they sit left of the label and shove it off-centre, and the HUD reads cleaner with plain centred text.
        for standard in (QDialogButtonBox.Ok, QDialogButtonBox.Cancel):
            button = self._buttons.button(standard)
            if button is not None:
                button.setIcon(QIcon())
        root.addWidget(self._buttons)

        # Paint the panel with the overlay's stylesheet at full opacity so the form stays legible over anything behind it; the extra rule drops the tab pane's outline so settings read as one borderless HUD surface.
        self.panel.setStyleSheet(
            theme.settings_panel_stylesheet(
                alpha=255, font_pt=config.font_point_size
            )
        )
        # Complete the About page's stock-orange homage in the chrome outside its page widget; other pages return the selected tab and dialog actions to the player's HUD colours.
        self.tabs.currentChanged.connect(self._sync_about_chrome)
        self._sync_about_chrome(self.tabs.currentIndex())

        self._about_easter_egg = AboutEasterEggController(
            self, self.panel, self._about_logo
        )

        # Size the window to the initial (General) tab; it re-fits on switch and again on first show (word-wrap heights need the real width).
        self._fit_to_current_tab(self.tabs.currentIndex())

    def showEvent(self, event) -> None:
        super().showEvent(event)
        self._fit_to_current_tab(self.tabs.currentIndex())

    def _fit_to_current_tab(self, index: int = -1) -> None:
        """Grow/shrink the window to the active tab so nothing ever scrolls; height is the visible page's own size hint plus fixed chrome, applied by explicit resize (the layouts' aggregate hint caches the tallest page, so the window would refuse to shrink from the About tab)."""
        page = self.tabs.currentWidget()
        if page is None:
            return
        self.resize(self.width(), self._chrome_height() + page.sizeHint().height())

    def _chrome_height(self) -> int:
        """Fixed vertical space around the tab pages: margins, title bar, tab bar, button box, and inter-widget spacing."""
        outer = self.layout().contentsMargins()
        panel = self._root.contentsMargins()
        gaps = self._root.spacing() * (self._root.count() - 1)
        return (
            outer.top() + outer.bottom()
            + panel.top() + panel.bottom()
            + gaps
            + self._title_bar.sizeHint().height()
            + self.tabs.tabBar().sizeHint().height()
            + self._buttons.sizeHint().height()
            + _FitTabs._PANE_SLACK
        )

    def _sync_about_chrome(self, index: int) -> None:
        """Use stock Elite colours for chrome belonging to the About page."""
        stylesheet = (
            theme.about_homage_chrome_stylesheet()
            if index == self._about_index
            else ""
        )
        self.tabs.tabBar().setStyleSheet(stylesheet)
        self._buttons.setStyleSheet(stylesheet)

    def _sync_gizmo_targeting(self, *_args) -> None:
        """Emit whether the gizmo aim crosshairs should be editable right now; they belong to the Controls tab and only make sense once gizmos are on, so both conditions gate them."""
        active = (
            self.tabs.currentIndex() == self._controls_index
            and self.gizmo_enabled.isChecked()
        )
        if active != self._gizmo_targeting:
            self._gizmo_targeting = active
            self.gizmo_targeting_changed.emit(active)

    #  chrome

    def _build_title_bar(self) -> QWidget:
        bar = _DragBar(self)
        row = QHBoxLayout(bar)
        row.setContentsMargins(*theme.METRICS.page_margins)
        title = QLabel("EDSC Settings")
        theme.set_role(title, theme.TITLE_ROLE)
        row.addWidget(title, 1)
        close = self._window_control("✕", "Close")
        close.clicked.connect(self.reject)
        row.addWidget(close)
        return bar

    def _window_control(self, text: str, tip: str) -> QPushButton:
        button = QPushButton(text)
        button.setToolTip(tip)
        theme.set_role(button, theme.WINDOW_CONTROL_ROLE)
        button.setFixedSize(*theme.METRICS.window_control_size)
        return button

    #  tab builders

    @staticmethod
    def _tab() -> tuple[QWidget, QFormLayout]:
        """A tab page carrying a form layout, returned as ``(page, form)``."""
        page = QWidget()
        form = QFormLayout(page)
        return page, form

    def _build_general_tab(self) -> QWidget:
        page, form = self._tab()

        # Journal directory override + auto-detect hint.
        self.journal_edit = QLineEdit(self.config.journal_dir)
        self.journal_edit.setPlaceholderText("(leave empty to auto-detect)")
        browse = QPushButton("Browse…")
        browse.clicked.connect(self._browse)
        path_row = QHBoxLayout()
        path_row.addWidget(self.journal_edit, 1)
        path_row.addWidget(browse)
        path_wrap = QWidget()
        path_wrap.setLayout(path_row)
        form.addRow("Journal folder:", path_wrap)

        detected = locator.find_journal_dir(self.config.journal_dir or None)
        hint = str(detected) if detected else "not found - set it manually above"
        detected_label = QLabel(f"Auto-detected: {hint}")
        detected_label.setWordWrap(True)
        theme.set_role(detected_label, theme.MUTED_ROLE)
        form.addRow("", detected_label)
        return page

    def _build_display_tab(self) -> QWidget:
        page, form = self._tab()

        # Opacity.
        self.opacity = QSlider(Qt.Horizontal)
        self.opacity.setRange(
            theme.METRICS.opacity_percent_minimum,
            theme.METRICS.opacity_percent_maximum,
        )
        self.opacity.setValue(int(self.config.overlay_opacity * 100))
        self.opacity_label = QLabel(f"{self.opacity.value()}%")
        self.opacity.valueChanged.connect(
            lambda v: self.opacity_label.setText(f"{v}%")
        )
        form.addRow("Overlay opacity:", self._slider_row(self.opacity, self.opacity_label))

        # Docked opacity can be enabled independently of the normal overlay opacity, so the user keeps a predictable in-flight appearance.
        self.auto_opacity_on_dock = QCheckBox("Automatic opacity change on dock")
        self.auto_opacity_on_dock.setChecked(self.config.auto_opacity_on_dock)
        form.addRow("", self.auto_opacity_on_dock)

        self.docked_opacity = QSlider(Qt.Horizontal)
        self.docked_opacity.setRange(
            theme.METRICS.opacity_percent_minimum,
            theme.METRICS.opacity_percent_maximum,
        )
        self.docked_opacity.setValue(int(self.config.docked_opacity * 100))
        self.docked_opacity_label = QLabel(f"{self.docked_opacity.value()}%")
        self.docked_opacity.valueChanged.connect(
            lambda v: self.docked_opacity_label.setText(f"{v}%")
        )
        self.docked_opacity_wrap = self._slider_row(
            self.docked_opacity, self.docked_opacity_label
        )
        self.docked_opacity_prompt = QLabel("Docked opacity:")
        form.addRow(self.docked_opacity_prompt, self.docked_opacity_wrap)
        self.auto_opacity_on_dock.toggled.connect(self._set_docked_opacity_enabled)
        self._set_docked_opacity_enabled(self.auto_opacity_on_dock.isChecked())

        self.auto_collapse_on_undock = QCheckBox(
            "Expand overlay on dock and collapse on liftoff"
        )
        self.auto_collapse_on_undock.setChecked(self.config.auto_collapse_on_undock)
        form.addRow("", self.auto_collapse_on_undock)

        # Font size.
        self.font_size = QSpinBox()
        self.font_size.setRange(
            theme.METRICS.font_point_minimum,
            theme.METRICS.font_point_maximum,
        )
        self.font_size.setValue(self.config.font_point_size)
        form.addRow("Font size:", self.font_size)

        # Toggles.
        self.always_on_top = QCheckBox("Keep overlay above other windows")
        self.always_on_top.setChecked(self.config.always_on_top)
        form.addRow("", self.always_on_top)

        self.auto_height = QCheckBox("Auto-fit height to the commodity list")
        self.auto_height.setChecked(self.config.auto_height)
        form.addRow("", self.auto_height)

        self.hide_completed = QCheckBox("Hide fully delivered commodities")
        self.hide_completed.setChecked(self.config.hide_completed)
        form.addRow("", self.hide_completed)
        return page

    def _build_construction_tab(self) -> QWidget:
        page, form = self._tab()
        note = QLabel(
            "Defaults for the station search that sources construction "
            "commodities. These mirror the toggles on the Stations tab."
        )
        note.setWordWrap(True)
        theme.set_role(note, theme.MUTED_ROLE)
        form.addRow(note)

        self.include_planets = QCheckBox(
            "Include planetary (surface) stations in results"
        )
        self.include_planets.setChecked(self.config.stations_include_planets)
        form.addRow("", self.include_planets)

        self.include_carriers = QCheckBox("Include fleet carriers in results")
        self.include_carriers.setChecked(self.config.stations_include_carriers)
        form.addRow("", self.include_carriers)

        self.recent_only = QCheckBox(
            "Only markets updated in the last 24 hours"
        )
        self.recent_only.setChecked(self.config.stations_recent_only)
        form.addRow("", self.recent_only)

        # How the ranked pool is ordered.
        self.stations_sort = self._sort_combo(
            CONSTRUCTION_SORTS, self.config.stations_sort
        )
        form.addRow("Sort by:", self.stations_sort)

        # Hard distance cap, with an "Unlimited" end-stop at the far right.
        self.stations_range = QSlider(Qt.Horizontal)
        self.stations_range.setRange(CONSTR_RANGE_MIN, CONSTR_RANGE_UNLIMITED)
        self.stations_range.setSingleStep(CONSTR_RANGE_STEP)
        self.stations_range.setPageStep(CONSTR_RANGE_STEP)
        self.stations_range.setValue(self._range_to_slider(self.config.stations_range_ly))
        self.stations_range_label = QLabel(
            self._range_text(self.stations_range.value())
        )
        self.stations_range.valueChanged.connect(
            lambda v: self.stations_range_label.setText(self._range_text(v))
        )
        form.addRow(
            "Search radius:",
            self._slider_row(self.stations_range, self.stations_range_label),
        )
        return page

    def _build_colonization_tab(self) -> QWidget:
        page, form = self._tab()
        note = QLabel(
            "Defaults for the colonizable-system search. Wide radii are for "
            "planning bridge-colony chains."
        )
        note.setWordWrap(True)
        theme.set_role(note, theme.MUTED_ROLE)
        form.addRow(note)

        self.colonize_range = QSlider(Qt.Horizontal)
        self.colonize_range.setRange(COLONIZE_RANGE_MIN, COLONIZE_RANGE_MAX)
        self.colonize_range.setValue(int(self.config.colonize_range_ly))
        self.colonize_range_label = QLabel(f"{self.colonize_range.value()} Ly")
        self.colonize_range.valueChanged.connect(
            lambda v: self.colonize_range_label.setText(f"{v} Ly")
        )
        form.addRow(
            "Search radius:",
            self._slider_row(self.colonize_range, self.colonize_range_label),
        )

        self.colonize_sort = self._sort_combo(
            COLONIZE_SORTS, self.config.colonize_sort
        )
        form.addRow("Sort by:", self.colonize_sort)

        # Body-richness weight feeds the "Balanced" ranking: 0 is pure distance, higher pulls body-rich systems up the list.
        self.colonize_weight = QSlider(Qt.Horizontal)
        self.colonize_weight.setRange(0, COLONIZE_WEIGHT_MAX)
        self.colonize_weight.setValue(self._weight_to_slider(self.config.colonize_body_weight))
        self.colonize_weight_label = QLabel(
            self._weight_text(self.colonize_weight.value())
        )
        self.colonize_weight.valueChanged.connect(
            lambda v: self.colonize_weight_label.setText(self._weight_text(v))
        )
        weight_row = self._slider_row(self.colonize_weight, self.colonize_weight_label)
        self.colonize_weight_prompt = QLabel("Body-richness weight:")
        form.addRow(self.colonize_weight_prompt, weight_row)

        # The weight only feeds the "Balanced" strategy; grey it out otherwise.
        self.colonize_sort.currentIndexChanged.connect(
            self._sync_colonize_weight_enabled
        )
        self._sync_colonize_weight_enabled()
        return page

    def _build_eddn_tab(self) -> QWidget:
        page, form = self._tab()
        note = QLabel(
            "EDDN is the community's Elite Dangerous Data Network. When enabled, "
            "EDSC relays the market and docking data you generate so tools like "
            "Spansh (which powers the station search) stay current. Only game "
            "data is shared, tagged with a random anonymous ID — never your "
            "commander name."
        )
        note.setWordWrap(True)
        theme.set_role(note, theme.MUTED_ROLE)
        form.addRow(note)

        self.eddn_enabled = QCheckBox("Share market and journal data with EDDN")
        self.eddn_enabled.setChecked(bool(self.config.eddn_enabled))
        form.addRow("", self.eddn_enabled)

        if self.config.eddn_uploader_id:
            uploader = QLabel(f"Anonymous ID: {self.config.eddn_uploader_id}")
            uploader.setWordWrap(True)
            uploader.setTextInteractionFlags(Qt.TextSelectableByMouse)
            theme.set_role(uploader, theme.MUTED_ROLE)
            form.addRow("", uploader)

        self._eddn_status_label = QLabel(self._eddn_status_text())
        self._eddn_status_label.setWordWrap(True)
        theme.set_role(self._eddn_status_label, theme.STATUS_ROLE)
        form.addRow("Last status:", self._eddn_status_label)

        # A live console of the last uploads: what was relayed and whether the gateway accepted it; read-only, monospaced, fixed to a few rows so the tab keeps a stable height.
        self._eddn_console = QPlainTextEdit()
        self._eddn_console.setReadOnly(True)
        self._eddn_console.setFont(theme.monospace_font(self.config.font_point_size))
        self._eddn_console.setLineWrapMode(QPlainTextEdit.NoWrap)
        # Fix the console to ~seven rows so the tab keeps a stable height however many events have landed.
        self._eddn_console.setFixedHeight(
            self._eddn_console.fontMetrics().lineSpacing() * 7 + 12
        )
        self._eddn_console.setPlaceholderText("No uploads yet this session.")
        form.addRow("Recent activity:", self._eddn_console)

        # A manual push for launching EDSC while already docked: the relay only sees live events, so nothing uploads until the next jump/dock -- "Sync now" force-shares the current market/position from disk. Disabled without a live relay.
        sync_row = QHBoxLayout()
        self._eddn_sync_button = QPushButton("Sync now")
        self._eddn_sync_button.setEnabled(self._on_eddn_sync is not None)
        self._eddn_sync_button.clicked.connect(self._on_eddn_sync_clicked)
        sync_row.addWidget(self._eddn_sync_button)
        self._eddn_sync_result = QLabel("")
        self._eddn_sync_result.setWordWrap(True)
        theme.set_role(self._eddn_sync_result, theme.MUTED_ROLE)
        sync_row.addWidget(self._eddn_sync_result, 1)
        form.addRow("", sync_row)

        # Repaint the console/status while the dialog is open so the player sees uploads land in real time; the timer runs only with a live relay (a config-only dialog shows a static summary).
        self._refresh_eddn_console()
        if self._eddn_uplink is not None:
            self._eddn_timer = QTimer(self)
            self._eddn_timer.setInterval(_EDDN_CONSOLE_REFRESH_MS)
            self._eddn_timer.timeout.connect(self._refresh_eddn_console)
            self._eddn_timer.start()
        return page

    def _refresh_eddn_console(self) -> None:
        """Repaint the status line and activity console from the live relay."""
        uplink = self._eddn_uplink
        if uplink is None:
            return
        self._eddn_status_label.setText(uplink.sender.status_line())
        # Newest first: most players glance at the top for the last upload.
        lines = [
            format_activity(entry)
            for entry in reversed(uplink.sender.activity.snapshot())
        ]
        text = "\n".join(lines)
        # Only rewrite on change so the player's scroll position/selection in a quiet console isn't reset every second.
        if text != self._eddn_console.toPlainText():
            self._eddn_console.setPlainText(text)

    def _on_eddn_sync_clicked(self) -> None:
        """Force a manual push and report what was queued next to the button."""
        if self._on_eddn_sync is None:
            return
        self._eddn_sync_button.setEnabled(False)
        try:
            result = self._on_eddn_sync()
        finally:
            self._eddn_sync_button.setEnabled(True)
        if result is None:
            self._eddn_sync_result.setText("Sharing is off.")
        else:
            journal_sent, market_sent = result
            queued = [
                name
                for name, sent in (("market", market_sent), ("location", journal_sent))
                if sent
            ]
            if queued:
                self._eddn_sync_result.setText(f"Queued: {', '.join(queued)}.")
            else:
                self._eddn_sync_result.setText(
                    "Nothing to share — dock at a station with market data first."
                )
        # Surface the queued rows in the console straight away.
        self._refresh_eddn_console()

    def _eddn_status_text(self) -> str:
        """Live uplink summary when running, else a state derived from config."""
        if self._eddn_status:
            return self._eddn_status
        if self.config.eddn_enabled:
            return "Sharing on — nothing uploaded yet this session."
        if self.config.eddn_enabled is None:
            return "Not configured — you have not been asked yet."
        return "Sharing off."

    def _build_controls_tab(self) -> QWidget:
        page, form = self._tab()

        # Auto-ignore the mouse (click-through) while the game is focused, and become movable again when it isn't.
        self.auto_click_through = QCheckBox(
            "Click-through while the game window is focused"
        )
        self.auto_click_through.setChecked(self.config.auto_click_through)
        form.addRow("", self.auto_click_through)

        # Substrings used to recognise the game window (class or title).
        self.matchers = QLineEdit(", ".join(self.config.game_window_matchers))
        self.matchers.setPlaceholderText(
            "steam_app_359320, elite - dangerous (client)"
        )
        form.addRow("Game window matches:", self.matchers)

        self.controller_tester = ControllerTesterWidget(
            self._controllers,
            selected_device_id=(
                self.config.controller_device_id
                if isinstance(self.config.controller_device_id, str)
                else ""
            ),
            bindings=self.config.controller_bindings,
            development_mode=self._development_mode,
        )
        form.addRow(self.controller_tester)
        self._build_gizmo_section(form)
        return page

    def _build_gizmo_section(self, form: QFormLayout) -> None:
        """Flight gizmo controls, alongside the rest of the controller setup."""
        title = QLabel("Flight gizmos")
        theme.set_role(title, theme.SUBTITLE_ROLE)
        form.addRow(title)

        note = QLabel(
            "Two floating indicators showing live thrust and rotation input. "
            "They sit above the game and ignore the mouse while it is focused; "
            "alt-tab to drag them somewhere else. While this tab is open, drag "
            "the crosshair on each gizmo to set the point it leans toward."
        )
        note.setWordWrap(True)
        theme.set_role(note, theme.MUTED_ROLE)
        form.addRow(note)

        self.gizmo_enabled = QCheckBox("Show the thrust and rotation gizmos")
        self.gizmo_enabled.setChecked(bool(self.config.gizmo_enabled))
        form.addRow("", self.gizmo_enabled)

        self.gizmo_in_flight_only = QCheckBox("Hide while docked")
        self.gizmo_in_flight_only.setChecked(bool(self.config.gizmo_in_flight_only))
        form.addRow("", self.gizmo_in_flight_only)

        self.gizmo_apply_deadzone = QCheckBox(
            "Apply the deadzones from your Elite bindings"
        )
        self.gizmo_apply_deadzone.setChecked(bool(self.config.gizmo_apply_deadzone))
        form.addRow("", self.gizmo_apply_deadzone)

        self.gizmo_scale = QSlider(Qt.Horizontal)
        self.gizmo_scale.setRange(_GIZMO_SCALE_MIN, _GIZMO_SCALE_MAX)
        self.gizmo_scale.setValue(self._gizmo_scale_to_slider(self.config.gizmo_scale))
        self.gizmo_scale_label = QLabel()
        self.gizmo_scale.valueChanged.connect(self._update_gizmo_scale_label)
        self._update_gizmo_scale_label(self.gizmo_scale.value())
        form.addRow(
            "Gizmo size:", self._slider_row(self.gizmo_scale, self.gizmo_scale_label)
        )

    @staticmethod
    def _gizmo_scale_to_slider(scale: object) -> int:
        try:
            percent = round(float(scale) * 100)
        except (TypeError, ValueError):
            percent = 100
        return max(_GIZMO_SCALE_MIN, min(_GIZMO_SCALE_MAX, percent))

    def _update_gizmo_scale_label(self, value: int) -> None:
        side = round(gizmo.BASE_SIZE * value / 100)
        self.gizmo_scale_label.setText(f"{side}px")

    def _build_about_tab(self) -> QWidget:
        # The About page is tall (emblem + powered-by marks + blurb); the window grows to fit rather than scrolling (see _fit_to_current_tab), with step-by-step tips on the neighbouring "?" tab.
        page = QWidget()
        # Homage: this one tab ignores the player's HUD colour matrix and stays stock Elite orange -- the emblem renders stock, and this scoped stylesheet overrides the matrix-derived label colours for the About subtree only.
        page.setStyleSheet(theme.about_homage_stylesheet())
        root = QVBoxLayout(page)
        root.setSpacing(theme.METRICS.content_spacing)

        emblem = ClickableImage()
        emblem.setPixmap(
            icons.app_glyph_pixmap(theme.METRICS.about_icon_px, stock=True)
        )
        emblem.setAlignment(Qt.AlignHCenter)
        theme.set_role(emblem, theme.IMAGE_ROLE)
        self._about_logo = emblem
        root.addWidget(emblem)

        title = QLabel(APP_FULL_NAME)
        theme.set_role(title, theme.TITLE_ROLE)
        title.setAlignment(Qt.AlignHCenter)
        root.addWidget(title)

        subtitle = QLabel(f"EDSC v{__version__} · GPLv3 · by CMDR FEEDMEWEED")
        theme.set_role(subtitle, theme.MUTED_ROLE)
        subtitle.setAlignment(Qt.AlignHCenter)
        root.addWidget(subtitle)

        root.addWidget(self._powered_by())

        blurb = QLabel(
            "A companion overlay for Elite: Dangerous that tracks the "
            "commodities your colonisation construction projects need against "
            "what you are carrying and what you have already delivered."
        )
        blurb.setWordWrap(True)
        root.addWidget(blurb)

        attribution = QLabel(_ATTRIBUTION)
        attribution.setWordWrap(True)
        theme.set_role(attribution, theme.MUTED_ROLE)
        root.addWidget(attribution)

        contact = QLabel(f"Bugs & suggestions: {CONTACT_EMAIL}\n{PROJECT_URL}")
        contact.setWordWrap(True)
        contact.setTextInteractionFlags(Qt.TextSelectableByMouse)
        theme.set_role(contact, theme.MUTED_ROLE)
        root.addWidget(contact)
        root.addStretch(1)
        return page

    def _powered_by(self) -> QWidget:
        """The "Powered by" credit strip: HUD-tinted service marks in a row."""
        box = QWidget()
        col = QVBoxLayout(box)
        col.setContentsMargins(0, 4, 0, 0)
        col.setSpacing(4)
        head = QLabel("Powered by")
        theme.set_role(head, theme.SUBTITLE_ROLE)
        head.setAlignment(Qt.AlignHCenter)
        col.addWidget(head)

        row = QHBoxLayout()
        row.setSpacing(20)
        row.addStretch(1)
        for name, caption in _POWERED_BY:
            item = QVBoxLayout()
            item.setSpacing(2)
            logo = QLabel()
            logo.setPixmap(icons.powered_logo_pixmap(name, 104, 44))
            logo.setFixedHeight(46)
            logo.setAlignment(Qt.AlignCenter)
            logo.setToolTip(caption)
            theme.set_role(logo, theme.IMAGE_ROLE)
            item.addWidget(logo)
            label = QLabel(caption)
            label.setAlignment(Qt.AlignHCenter)
            theme.set_role(label, theme.MUTED_ROLE)
            item.addWidget(label)
            wrap = QWidget()
            wrap.setLayout(item)
            row.addWidget(wrap)
        row.addStretch(1)
        row_wrap = QWidget()
        row_wrap.setLayout(row)
        col.addWidget(row_wrap)
        return box

    def _build_help_tab(self) -> QWidget:
        """The "?" tab: quick tips as compact glyph cards; the two groups sit in side-by-side columns so the whole cheat-sheet fits one screen (a single stacked column runs ~1100px and would overflow 1080p)."""
        page = QWidget()
        row = QHBoxLayout(page)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(theme.METRICS.content_spacing * 2)
        for heading, tips in (
            ("Getting started", _GETTING_STARTED),
            ("Good to know", _GOOD_TO_KNOW),
        ):
            column = QVBoxLayout()
            column.setSpacing(theme.METRICS.content_spacing)
            head = QLabel(heading)
            theme.set_role(head, theme.SUBTITLE_ROLE)
            column.addWidget(head)
            for glyph, title, body in tips:
                column.addWidget(self._tip_card(glyph, title, body))
            column.addStretch(1)
            row.addLayout(column, 1)
        return page

    def _tip_card(self, glyph: str, title: str, body: str) -> QWidget:
        """One tip: a HUD glyph beside a bold title over a concise wrapped line."""
        card = QFrame()
        theme.set_role(card, theme.TIP_CARD_ROLE)
        row = QHBoxLayout(card)
        row.setContentsMargins(8, 6, 8, 6)
        row.setSpacing(10)

        icon = QLabel(glyph)
        theme.set_role(icon, theme.TIP_GLYPH_ROLE)
        icon.setFixedWidth(28)
        icon.setAlignment(Qt.AlignHCenter | Qt.AlignTop)
        row.addWidget(icon)

        col = QVBoxLayout()
        col.setContentsMargins(0, 0, 0, 0)
        col.setSpacing(1)
        head = QLabel(title)
        theme.set_role(head, theme.TIP_TITLE_ROLE)
        col.addWidget(head)
        text = QLabel(body)
        text.setWordWrap(True)
        theme.set_role(text, theme.MUTED_ROLE)
        col.addWidget(text)
        row.addLayout(col, 1)
        return card

    #  helpers

    @staticmethod
    def _slider_row(slider: QSlider, value_label: QLabel) -> QWidget:
        """Pack a slider and its live value label into one form-row widget."""
        row = QHBoxLayout()
        row.addWidget(slider, 1)
        row.addWidget(value_label)
        wrap = QWidget()
        wrap.setLayout(row)
        return wrap

    @staticmethod
    def _sort_combo(options: tuple[tuple[str, str], ...], current: str) -> QComboBox:
        """A dropdown of (label, value) options with ``current`` preselected."""
        combo = QComboBox()
        for label, value in options:
            combo.addItem(label, value)
        index = combo.findData(current)
        combo.setCurrentIndex(index if index >= 0 else 0)
        return combo

    @staticmethod
    def _range_to_slider(range_ly: int) -> int:
        """Config radius (0 = unlimited) -> slider position."""
        if range_ly <= 0:
            return CONSTR_RANGE_UNLIMITED
        return max(CONSTR_RANGE_MIN, min(CONSTR_RANGE_MAX, range_ly))

    @staticmethod
    def _slider_to_range(value: int) -> int:
        """Slider position -> config radius (0 = unlimited)."""
        return 0 if value > CONSTR_RANGE_MAX else value

    @staticmethod
    def _range_text(value: int) -> str:
        return "Unlimited" if value > CONSTR_RANGE_MAX else f"{value} Ly"

    @staticmethod
    def _weight_to_slider(weight: float) -> int:
        return max(0, min(COLONIZE_WEIGHT_MAX, round(weight * COLONIZE_WEIGHT_SCALE)))

    @staticmethod
    def _slider_to_weight(value: int) -> float:
        return value / COLONIZE_WEIGHT_SCALE

    @staticmethod
    def _weight_text(value: int) -> str:
        return f"{value / COLONIZE_WEIGHT_SCALE:.1f}×"

    def _sync_colonize_weight_enabled(self) -> None:
        """The body-richness weight only feeds the Balanced ranking."""
        enabled = self.colonize_sort.currentData() == "balanced"
        self.colonize_weight_prompt.setEnabled(enabled)
        self.colonize_weight.setEnabled(enabled)
        self.colonize_weight_label.setEnabled(enabled)

    def _browse(self) -> None:
        start = self.journal_edit.text() or ""
        chosen = QFileDialog.getExistingDirectory(self, "Select journal folder", start)
        if chosen:
            self.journal_edit.setText(chosen)

    def _set_docked_opacity_enabled(self, enabled: bool) -> None:
        """Enable the docked-opacity control only when it can take effect."""
        self.docked_opacity_prompt.setEnabled(enabled)
        self.docked_opacity_wrap.setEnabled(enabled)

    def apply_to(self, config: Config) -> None:
        """Write the dialog's values back into ``config``."""
        config.journal_dir = self.journal_edit.text().strip()
        config.overlay_opacity = self.opacity.value() / 100.0
        config.auto_opacity_on_dock = self.auto_opacity_on_dock.isChecked()
        config.docked_opacity = self.docked_opacity.value() / 100.0
        config.auto_collapse_on_undock = self.auto_collapse_on_undock.isChecked()
        config.font_point_size = self.font_size.value()
        config.always_on_top = self.always_on_top.isChecked()
        config.auto_height = self.auto_height.isChecked()
        config.hide_completed = self.hide_completed.isChecked()
        config.stations_include_planets = self.include_planets.isChecked()
        config.stations_include_carriers = self.include_carriers.isChecked()
        config.stations_recent_only = self.recent_only.isChecked()
        config.stations_sort = self.stations_sort.currentData()
        config.stations_range_ly = self._slider_to_range(self.stations_range.value())
        config.colonize_range_ly = self.colonize_range.value()
        config.colonize_sort = self.colonize_sort.currentData()
        config.colonize_body_weight = self._slider_to_weight(
            self.colonize_weight.value()
        )
        config.eddn_enabled = self.eddn_enabled.isChecked()
        # Mint the anonymous per-install uploader ID the first time sharing is turned on, so the uplink has an identity ready.
        if config.eddn_enabled and not config.eddn_uploader_id:
            config.eddn_uploader_id = str(uuid.uuid4())
        config.auto_click_through = self.auto_click_through.isChecked()
        config.game_window_matchers = [
            m.strip() for m in self.matchers.text().split(",") if m.strip()
        ]
        config.controller_device_id = (
            self.controller_tester.selected_device_id or ""
        )
        config.controller_bindings = self.controller_tester.binding_config
        config.gizmo_enabled = self.gizmo_enabled.isChecked()
        config.gizmo_in_flight_only = self.gizmo_in_flight_only.isChecked()
        config.gizmo_apply_deadzone = self.gizmo_apply_deadzone.isChecked()
        config.gizmo_scale = self.gizmo_scale.value() / 100.0
