"""Qt table model presenting a project's commodities.


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

from PySide6.QtCore import QAbstractTableModel, QModelIndex, Qt
from PySide6.QtGui import QColor, QFont

from ..model import CommodityRow
from ..stations import StationResult
from . import theme

COLUMNS = ["Commodity", "Need", "Hold", "Carrier", "Done", "Short"]
_NUMERIC_COLS = {1, 2, 3, 4, 5}
_HOLD_COL = 2
_CARRIER_COL = 3
_SHORT_COL = 5


class CommodityTableModel(QAbstractTableModel):
    def __init__(self) -> None:
        super().__init__()
        self._rows: list[CommodityRow] = []
        self._hide_completed = False
        self._mono = QFont("monospace")
        self._mono.setStyleHint(QFont.Monospace)

    #  data feed 

    def set_rows(self, rows: list[CommodityRow]) -> None:
        self.beginResetModel()
        self._rows = [r for r in rows if not (self._hide_completed and r.done)]
        self.endResetModel()

    def set_hide_completed(self, hide: bool) -> None:
        self._hide_completed = hide

    def row_at(self, row: int) -> CommodityRow | None:
        return self._rows[row] if 0 <= row < len(self._rows) else None

    #  Qt model interface

    def rowCount(self, parent: QModelIndex = QModelIndex()) -> int:
        return 0 if parent.isValid() else len(self._rows)

    def columnCount(self, parent: QModelIndex = QModelIndex()) -> int:
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
        if col == _SHORT_COL:  # red if you must buy more, green if hold covers it
            return theme.READY if r.can_complete_now else theme.SHORT
        if col == _HOLD_COL and r.can_complete_now:
            return theme.READY  # hold alone covers what's still needed
        if col == _CARRIER_COL:
            if r.on_carrier == 0:
                return theme.TEXT_DIM
            # green when hold+carrier together cover the remainder
            return theme.READY if r.covered_by_stock else theme.TEXT
        return theme.TEXT


# No pad column: the search is restricted to large-pad stations already.
STATION_COLUMNS = ["Station", "System", "Match/Coverage", "Ly", "Arrival"]
ST_SYSTEM_COL = 1  # clicking this column copies the system name (see overlay)
_ST_NUMERIC_COLS = {2, 3, 4}
_ST_MATCH_COL = 2


class StationTableModel(QAbstractTableModel):
    """Presents ranked nearest-station results from the Spansh search."""

    def __init__(self) -> None:
        super().__init__()
        self._rows: list[StationResult] = []
        self._mono = QFont("monospace")
        self._mono.setStyleHint(QFont.Monospace)

    #  data feed 

    def set_rows(self, rows: list[StationResult]) -> None:
        self.beginResetModel()
        self._rows = list(rows)
        self.endResetModel()

    def row_at(self, row: int) -> StationResult | None:
        return self._rows[row] if 0 <= row < len(self._rows) else None

    #  Qt model interface

    def rowCount(self, parent: QModelIndex = QModelIndex()) -> int:
        return 0 if parent.isValid() else len(self._rows)

    def columnCount(self, parent: QModelIndex = QModelIndex()) -> int:
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
            # ● surface station, ▻, carriers, ★ orbital - mirrors the tooltip's kind line.
            marker = "●" if s.is_planetary else "▻" if s.is_carrier else "★"
            return (
                f"{marker} {s.name}",
                s.system,
                f"{s.match_count}/{s.needed_total} ({s.coverage * 100:.0f}%)",
                f"{s.distance_ly:,.1f}",
                _format_ls(s.arrival_ls),
            )[col]

        if role == Qt.ToolTipRole:
            stocked = ", ".join(sorted(s.matched))
            kind = "Planetary" if s.is_planetary else "Carrier" if s.is_carrier else "Orbital"
            updated = s.market_updated_at or "unknown"
            hint = "\nClick to copy the system name" if col == ST_SYSTEM_COL else ""
            return (
                f"{s.name} - {s.system}\n"
                f"{kind} · {s.station_type or 'Station'}\n"
                f"Market data from: {updated}\n"
                f"Stocks {s.match_count} of {s.needed_total} needed:\n{stocked}"
                f"{hint}"
            )

        if role == Qt.TextAlignmentRole and col in _ST_NUMERIC_COLS:
            return int(Qt.AlignRight | Qt.AlignVCenter)

        if role == Qt.FontRole and col in _ST_NUMERIC_COLS:
            return self._mono

        if role == Qt.ForegroundRole:
            return self._foreground(s, col)

        return None

    def _foreground(self, s: StationResult, col: int) -> QColor:
        if col == _ST_MATCH_COL:
            if s.coverage >= 0.999:
                return theme.DONE
            if s.coverage >= 0.5:
                return theme.READY
            return theme.SHORT
        return theme.TEXT


def _format_ls(ls: float) -> str:
    """Compact supercruise arrival distance, e.g. 146, 2.3k, 58k Ls."""
    if ls >= 1000:
        return f"{ls / 1000:,.1f}k"
    return f"{ls:,.0f}"

