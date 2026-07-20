"""Qt models for commodity, station, and colonization tables."""

# SPDX-License-Identifier: GPL-3.0-or-later

from __future__ import annotations

from PySide6.QtCore import QAbstractTableModel, QModelIndex, Qt
from PySide6.QtGui import QColor

from ..model import CommodityRow
from ..station_planning import StationResult
from ..systems import SystemResult
from ..time_utils import parse_timestamp
from . import icons, theme
from .tooltips import (
    format_ls as _format_ls,
    station_tooltip as _station_tooltip,
    system_tooltip as _system_tooltip,
)

COLUMNS = ["Commodity", "Need", "Hold", "Carrier", "Done", "Short"]
_NUMERIC_COLS = {1, 2, 3, 4, 5}
_NAME_COL = 0
_HOLD_COL = 2
_CARRIER_COL = 3
_SHORT_COL = 5
_ROOT_INDEX = QModelIndex()
formatted_date = parse_timestamp


class CommodityTableModel(QAbstractTableModel):
    def __init__(self) -> None:
        super().__init__()
        self._rows: list[CommodityRow] = []
        self._station_stock: set[str] = set()
        self._hide_completed = False
        self._mono = theme.monospace_font()

    def set_rows(self, rows: list[CommodityRow]) -> None:
        self.beginResetModel()
        self._rows = [r for r in rows if not (self._hide_completed and r.done)]
        self.endResetModel()

    def set_station_stock(self, keys: set[str]) -> None:
        """Commodities in stock at the currently docked station; their names are highlighted so you can see at a glance what can be bought without leaving the pad."""
        if keys == self._station_stock:
            return
        self._station_stock = set(keys)
        if self._rows:
            self.dataChanged.emit(
                self.index(0, _NAME_COL),
                self.index(len(self._rows) - 1, _NAME_COL),
                [Qt.ForegroundRole],
            )

    def set_hide_completed(self, hide: bool) -> None:
        self._hide_completed = hide

    def row_at(self, row: int) -> CommodityRow | None:
        return self._rows[row] if 0 <= row < len(self._rows) else None

    def rowCount(self, parent: QModelIndex = _ROOT_INDEX) -> int:
        return 0 if parent.isValid() else len(self._rows)

    def columnCount(self, parent: QModelIndex = _ROOT_INDEX) -> int:
        return 0 if parent.isValid() else len(COLUMNS)

    def headerData(self, section, orientation, role=Qt.DisplayRole):
        if role != Qt.DisplayRole:
            return None
        if orientation == Qt.Horizontal:
            return COLUMNS[section]
        return None

    def data(self, index: QModelIndex, role=Qt.DisplayRole):
        if not index.isValid():
            return None
        r = self._rows[index.row()]
        col = index.column()

        if role == Qt.DisplayRole:
            return (
                r.name,
                f"{r.required:,}",
                f"{r.in_cargo:,}" if r.in_cargo else "-",
                f"{r.on_carrier:,}" if r.on_carrier else "-",
                f"{r.provided:,}",
                f"{r.short:,}" if r.short else ("✓" if r.done else "0"),
            )[col]

        if role == Qt.TextAlignmentRole and col in _NUMERIC_COLS:
            return int(Qt.AlignRight | Qt.AlignVCenter)

        if role == Qt.FontRole and col in _NUMERIC_COLS:
            return self._mono

        if role == Qt.ForegroundRole:
            return self._foreground(r, col)

        return None

    def _foreground(self, r: CommodityRow, col: int) -> QColor:
        if r.done:
            return theme.DONE if col in (0, _SHORT_COL) else theme.TEXT_DIM
        if col == _NAME_COL and r.key in self._station_stock:
            # In stock at the station we're docked at.
            return theme.READY
        if col == _SHORT_COL:  # red if you must buy more, green if hold covers it
            return theme.READY if r.can_complete_now else theme.SHORT
        if col == _HOLD_COL and r.can_complete_now:
            return theme.READY  # hold alone covers what's still needed
        if col == _CARRIER_COL:
            if r.on_carrier == 0:
                return theme.TEXT_DIM
            # green when hold+carrier together cover the remainder
            return theme.READY if r.covered_by_stock else theme.ORANGE
        return theme.ORANGE


# No pad column: the search is restricted to large-pad stations already.
STATION_COLUMNS = ["Station", "System", "Match", "Cov", "Ly", "Arrival"]
ST_SYSTEM_COL = 1  # clicking this column copies the system name (see overlay)
ST_MATCH_COL = 2
ST_COVER_COL = 3
ST_DIST_COL = 4
ST_ARRIVAL_COL = 5
_ST_NUMERIC_COLS = {ST_MATCH_COL, ST_COVER_COL, ST_DIST_COL, ST_ARRIVAL_COL}


class StationTableModel(QAbstractTableModel):
    """Presents ranked nearest-station results from the Spansh search."""

    def __init__(self) -> None:
        super().__init__()
        self._rows: list[StationResult] = []
        self._mono = theme.monospace_font()

    def set_rows(self, rows: list[StationResult]) -> None:
        self.beginResetModel()
        self._rows = list(rows)
        self.endResetModel()

    def row_at(self, row: int) -> StationResult | None:
        return self._rows[row] if 0 <= row < len(self._rows) else None

    def rowCount(self, parent: QModelIndex = _ROOT_INDEX) -> int:
        return 0 if parent.isValid() else len(self._rows)

    def columnCount(self, parent: QModelIndex = _ROOT_INDEX) -> int:
        return 0 if parent.isValid() else len(STATION_COLUMNS)

    def headerData(self, section, orientation, role=Qt.DisplayRole):
        if role != Qt.DisplayRole:
            return None
        if orientation == Qt.Horizontal:
            return STATION_COLUMNS[section]
        return None

    def data(self, index: QModelIndex, role=Qt.DisplayRole):
        if not index.isValid():
            return None
        s = self._rows[index.row()]
        col = index.column()

        if role == Qt.DisplayRole:
            # A carrier's vanity name joins the callsign, as on the tooltip's title line (the game presents them as one name).
            name = s.name
            if s.is_carrier and s.owner:
                name = f"{name} · {s.owner}"
            return (
                name,
                s.system,
                f"{s.match_count}/{s.needed_total}",
                f"{s.coverage * 100:.0f}%",
                f"{s.distance_ly:,.1f}",
                _format_ls(s.arrival_ls),
            )[col]

        if role == Qt.DecorationRole and col == 0:
            # Station-type sprite, tinted to the HUD (mirrors the tooltip).
            return icons.station_icon(s)

        if role == Qt.ToolTipRole:
            return _station_tooltip(s, copy_hint=col == ST_SYSTEM_COL)

        if role == Qt.TextAlignmentRole and col in _ST_NUMERIC_COLS:
            return int(Qt.AlignRight | Qt.AlignVCenter)

        if role == Qt.FontRole and col in _ST_NUMERIC_COLS:
            return self._mono

        if role == Qt.ForegroundRole:
            return self._foreground(s, col)

        return None

    def _foreground(self, s: StationResult, col: int) -> QColor:
        if col in (ST_MATCH_COL, ST_COVER_COL):
            if s.coverage >= 0.999:
                return theme.DONE
            if s.coverage >= 0.5:
                return theme.READY
            return theme.SHORT
        return theme.ORANGE


# Colonization candidates; Spansh data can be missing per field (unscanned systems), so every unknown renders as a dimmed "?" instead of a guess.
SYSTEM_COLUMNS = ["System", "Ly", "Steps", "Stars", "Bodies", "Furthest", "Agent"]
SY_SYSTEM_COL = 0  # clicking this column copies the system name (see overlay)
SY_DIST_COL = 1
SY_STEPS_COL = 2
SY_STARS_COL = 3
SY_BODIES_COL = 4
SY_FURTHEST_COL = 5
SY_AGENT_COL = 6
_SY_NUMERIC_COLS = {
    SY_DIST_COL, SY_STEPS_COL, SY_STARS_COL, SY_BODIES_COL, SY_FURTHEST_COL,
}
_UNKNOWN = "?"


class SystemTableModel(QAbstractTableModel):
    """Presents ranked colonization candidates from the Spansh search."""

    def __init__(self) -> None:
        super().__init__()
        self._rows: list[SystemResult] = []
        self._mono = theme.monospace_font()

    def set_rows(self, rows: list[SystemResult]) -> None:
        self.beginResetModel()
        self._rows = list(rows)
        self.endResetModel()

    def row_at(self, row: int) -> SystemResult | None:
        return self._rows[row] if 0 <= row < len(self._rows) else None

    def rowCount(self, parent: QModelIndex = _ROOT_INDEX) -> int:
        return 0 if parent.isValid() else len(self._rows)

    def columnCount(self, parent: QModelIndex = _ROOT_INDEX) -> int:
        return 0 if parent.isValid() else len(SYSTEM_COLUMNS)

    def headerData(self, section, orientation, role=Qt.DisplayRole):
        if role != Qt.DisplayRole:
            return None
        if orientation == Qt.Horizontal:
            return SYSTEM_COLUMNS[section]
        return None

    def data(self, index: QModelIndex, role=Qt.DisplayRole):
        if not index.isValid():
            return None
        s = self._rows[index.row()]
        col = index.column()

        if role == Qt.DisplayRole:
            return self._display(s, col)

        if role == Qt.ToolTipRole:
            return _system_tooltip(s, copy_hint=col == SY_SYSTEM_COL)

        if role == Qt.TextAlignmentRole and col in _SY_NUMERIC_COLS:
            return int(Qt.AlignRight | Qt.AlignVCenter)

        if role == Qt.FontRole and col in _SY_NUMERIC_COLS:
            return self._mono

        if role == Qt.ForegroundRole:
            return self._foreground(s, col)

        return None

    @staticmethod
    def _display(s: SystemResult, col: int) -> str:
        if col == SY_AGENT_COL:
            if s.agent is not None:
                return f"{s.agent.name} · {s.agent.distance_ly:,.1f} Ly"
            return _UNKNOWN if s.agent_error else "none"
        return (
            s.name or _UNKNOWN,
            f"{s.distance_ly:,.1f}" if s.distance_ly is not None else _UNKNOWN,
            str(s.steps) if s.steps is not None else _UNKNOWN,
            str(s.star_count) if s.star_count is not None else _UNKNOWN,
            (
                f"{s.known_body_count:,}"
                if s.known_body_count is not None
                else _UNKNOWN
            ),
            _format_ls(s.furthest_ls) if s.furthest_ls is not None else _UNKNOWN,
        )[col]

    def _foreground(self, s: SystemResult, col: int) -> QColor:
        # Unconfirmed by Raven Colonial: render the whole row dimmed so a hypothetical (Spansh-only) candidate reads as such at a glance; only a definitive "not found" dims, an unchecked/errored row (None) keeps the normal rich colouring below.
        if s.verified is False:
            if self._display(s, col) == _UNKNOWN:
                return theme.TEXT_DIM
            return theme.ORANGE_DIM
        if col == SY_STEPS_COL and s.steps is not None:
            if s.steps == 1:
                return theme.DONE  # claim range of populated space right now
            if s.steps <= 3:
                return theme.READY
            return theme.ORANGE
        if col == SY_AGENT_COL:
            if s.agent_error:
                return theme.TEXT_DIM
            return theme.DONE if s.claimable else theme.SHORT
        if self._display(s, col) == _UNKNOWN:
            return theme.TEXT_DIM
        return theme.ORANGE

