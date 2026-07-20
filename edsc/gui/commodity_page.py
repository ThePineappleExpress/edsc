"""The commodity list for one construction (or the combined view)."""

# SPDX-License-Identifier: GPL-3.0-or-later

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QProgressBar,
    QTableView,
    QVBoxLayout,
    QWidget,
)

from ..config import Config
from ..model import COMBINED_MARKET_ID, AppState, CommodityRow, Project
from . import icons, theme
from .carrier_dialog import CarrierCargoDialog
from .table_model import CommodityTableModel
from .widgets import ElideLabel, FittedTable, tool_button


class CommodityPage(QWidget):
    """What a construction still needs, and what the carrier already holds."""

    # The user edited tracked carrier cargo -> persist and re-render.
    carrier_edited = Signal()
    # The user cleared a finished construction (its market id).
    complete_requested = Signal(object)
    # A display toggle changed the row set -> the shell must re-render.
    rerender_requested = Signal()

    def __init__(self, config: Config) -> None:
        super().__init__()
        self.config = config
        self._state: AppState | None = None
        self._current_rows: list[CommodityRow] = []
        self._project: Project | None = None
        self.title = ""
        self.subtitle = ""

        root = QVBoxLayout(self)
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
        self.table = FittedTable()
        self.table.setModel(self.model)
        theme.configure_table(self.table, font_pt=self.config.font_point_size)
        self.table.setSelectionMode(QTableView.NoSelection)
        self.table.setFocusPolicy(Qt.NoFocus)
        self.table.verticalHeader().setVisible(False)
        hdr = self.table.horizontalHeader()
        hdr.setSectionResizeMode(0, QHeaderView.Stretch)
        for c in (1, 2, 3, 4, 5):
            hdr.setSectionResizeMode(c, QHeaderView.ResizeToContents)
        # Stretch 1: the list takes any extra height, so the progress bar stays at the top and the footer/carrier rows at the bottom.
        root.addWidget(self.table, 1)

        # Shown only when every commodity of the viewed construction is delivered: clicking removes the finished project (and its tab) so completed sites don't linger in the overlay.
        self.complete_btn = tool_button(
            "✔ Complete construction",
            "All commodities delivered - remove this construction from the overlay",
        )
        theme.set_role(self.complete_btn, theme.COMPLETE_BUTTON_ROLE)
        self.complete_btn.clicked.connect(self._complete_construction)
        self.complete_btn.setVisible(False)
        root.addWidget(self.complete_btn)

        # Footer: totals + toggles; the totals elide like the header labels, since every text that varies with project data must stay out of the minimum-width calculation.
        footer = QHBoxLayout()
        self.totals_label = ElideLabel()
        theme.set_role(self.totals_label, theme.SUBTITLE_ROLE)
        footer.addWidget(self.totals_label, 1)
        self.carrier_btn = tool_button("FC…", "Set fleet-carrier cargo amounts")
        self.carrier_btn.clicked.connect(self._edit_carrier_cargo)
        footer.addWidget(self.carrier_btn)
        self.hide_done_btn = tool_button(
            "Hide done", "Hide fully delivered items", checkable=True
        )
        self.hide_done_btn.setChecked(self.config.hide_completed)
        self.hide_done_btn.toggled.connect(self._toggle_hide_done)
        footer.addWidget(self.hide_done_btn)
        root.addLayout(footer)

        # Fleet-carrier tracking summary (hidden until a carrier is known).
        self.carrier_label = QLabel("")
        theme.set_role(self.carrier_label, theme.STATUS_ROLE)
        self.carrier_label.setWordWrap(True)
        root.addWidget(self.carrier_label)

    #  shell contract

    @property
    def fit_table(self) -> FittedTable:
        return self.table

    def header_icon(self):
        """The emblem for the header, or None to hide it (the empty view)."""
        return None if self._project is None else icons.colonization_icon(32)

    def apply_appearance(self) -> None:
        theme.update_table_metrics(self.table, self.config.font_point_size)

    def sync_from_config(self) -> None:
        self.hide_done_btn.blockSignals(True)
        self.hide_done_btn.setChecked(self.config.hide_completed)
        self.hide_done_btn.blockSignals(False)
        self.model.set_hide_completed(self.config.hide_completed)

    #  rendering

    def render(
        self, proj: Project, state: AppState, subtitle: str | None = None
    ) -> None:
        self._state = state
        self._project = proj
        status = (
            " · COMPLETE" if proj.complete else (" · FAILED" if proj.failed else "")
        )
        self.title = proj.station_name or proj.title
        self.subtitle = (
            subtitle if subtitle is not None else (proj.system_name or "") + status
        )

        frac = proj.progress_fraction()
        self.progress.setValue(round(frac * 100))
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
        # Offer to clear the finished site (real projects only: the combined view aggregates several sites and can't be "completed" as one).
        self.complete_btn.setVisible(
            proj.market_id != COMBINED_MARKET_ID
            and (proj.complete or proj.all_delivered)
        )
        self._update_carrier_label(state)

    def render_empty(self, state: AppState | None = None) -> None:
        self._state = state
        self._project = None
        self.title = "No construction projects yet"
        self.subtitle = "Dock at a colonisation construction site"
        self.progress.setValue(0)
        self.percent_label.setText("0%")
        self._current_rows = []
        self.model.set_rows([])
        self.totals_label.setText("")
        self.complete_btn.setVisible(False)
        self._update_carrier_label(state)

    def _update_carrier_label(self, state: AppState | None) -> None:
        """Show tracked carrier tonnage against what's aboard and what fits."""
        if state is None or (not state.carrier_callsign and not state.carrier_cargo):
            self.carrier_label.setVisible(False)
            self.carrier_btn.setVisible(bool(state and state.projects))
            return
        tracked = state.carrier_tracked_total()
        who = (
            f"FC {state.carrier_callsign}"
            if state.carrier_callsign
            else "Fleet carrier"
        )
        text = f"{who} · tracking {tracked:,} t"
        if state.carrier_total and tracked > state.carrier_total:
            # Tracking more than the carrier holds is a definite error (e.g. stock sold via the carrier market, which journals don't report as a transfer): the table would show phantom coverage.
            text += f" - exceeds the {state.carrier_total:,} t aboard; 'FC…' to correct"
        elif state.carrier_total and tracked < state.carrier_total:
            # Usually just unrelated cargo (trade goods etc.); informational.
            text += f" · {state.carrier_total - tracked:,} t aboard untracked"
        if state.carrier_capacity:
            # Capacity is what's left after crew services and packs, so this grows when services are uninstalled.
            text += (
                f" · {state.carrier_free_space():,} t free"
                f" of {state.carrier_capacity:,} t"
            )
        self.carrier_label.setText(text)
        self.carrier_label.setVisible(True)
        self.carrier_btn.setVisible(True)

    #  actions

    def _complete_construction(self) -> None:
        """Clear the finished construction in view (and its tab)."""
        if self._project is None:
            return
        self.complete_requested.emit(self._project.market_id)

    def _edit_carrier_cargo(self) -> None:
        """Open the manual carrier-cargo dialog for the commodities in view."""
        if self._state is None or not self._current_rows:
            return
        dialog = CarrierCargoDialog(self._current_rows, self)
        if dialog.exec():
            for key, amount in dialog.values().items():
                self._state.set_carrier_amount(key, amount)
            self.carrier_edited.emit()

    def _toggle_hide_done(self, checked: bool) -> None:
        self.config.hide_completed = checked
        # The model filters in set_rows, so the shell has to re-render the page for a toggle to take effect.
        self.model.set_hide_completed(checked)
        self.rerender_requested.emit()
