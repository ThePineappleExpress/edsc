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

from html import escape
from datetime import datetime, timedelta, timezone
from PySide6.QtCore import QAbstractTableModel, QModelIndex, Qt
from PySide6.QtGui import QColor

from ..model import CommodityRow
from ..stations import StationResult
from . import icons, theme

COLUMNS = ["Commodity", "Need", "Hold", "Carrier", "Done", "Short"]
_NUMERIC_COLS = {1, 2, 3, 4, 5}
_NAME_COL = 0
_HOLD_COL = 2
_CARRIER_COL = 3
_SHORT_COL = 5


class CommodityTableModel(QAbstractTableModel):
    def __init__(self) -> None:
        super().__init__()
        self._rows: list[CommodityRow] = []
        self._station_stock: set[str] = set()
        self._hide_completed = False
        self._mono = theme.monospace_font()

    #  data feed

    def set_rows(self, rows: list[CommodityRow]) -> None:
        self.beginResetModel()
        self._rows = [r for r in rows if not (self._hide_completed and r.done)]
        self.endResetModel()

    def set_station_stock(self, keys: set[str]) -> None:
        """Commodities in stock at the currently docked station.

        Their names are highlighted so you can see at a glance what can be
        bought without leaving the pad.
        """
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
            # A carrier's vanity name joins the callsign, as on the tooltip's
            # title line (the game presents them as one name).
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


def formatted_date(date: str) -> datetime | None:
    """Parse a Spansh ISO-8601 date, or return ``None`` if it is unusable."""
    if not isinstance(date, str) or not date.strip():
        return None
    value = date.strip()
    # Python 3.10's fromisoformat() does not accept the UTC ``Z`` suffix.
    if value.endswith("Z"):
        value = f"{value[:-1]}+00:00"
    # Spansh also emits short offsets such as ``+00``; normalize those to
    # the form accepted consistently across all supported Python versions.
    elif ("T" in value or " " in value) and value[-3:-2] in ("+", "-"):
        if value[-2:].isdigit():
            value = f"{value}:00"
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _station_tooltip(s: StationResult, copy_hint: bool) -> str:
    """Rich-text station tooltip: the identity header with the type sprite and
    market freshness, then the needed commodities as coloured stocked/missing
    lists with each entry's stocked share of the requirement.
    """
    kind = "Planetary" if s.is_planetary else "Carrier" if s.is_carrier else "Orbital"

    def entry(name: str) -> tuple[str, str, bool]:
        demand = s.demand_by_name.get(name, 0)
        if demand <= 0:  # amount-less search: no percentage to show
            return escape(name), "", True
        stocked = min(100, round(100 * s.supply_by_name.get(name, 0) / demand))
        return escape(name), f"{stocked}%", stocked >= 100

    stock = [entry(n) for n in sorted(s.matched)]
    missing = [escape(n) for n in sorted(s.missing)]
    hint = "<br>Click to copy the system name" if copy_hint else ""
    # Calculate time elapsed from market update to now
    updated_at = formatted_date(s.market_updated_at)
    freshness = datetime.now(timezone.utc) - updated_at if updated_at else None
    def _elapsed(freshness: timedelta) -> str:
        """Return a human-readable string for the elapsed time."""
        if freshness is None:
            return "unknown"
        days = freshness.days
        seconds = freshness.seconds
        hours = seconds // 3600
        minutes = (seconds % 3600) // 60
        if days >= 365:
            years = days // 365
            return f"{years} year{'s' if years != 1 else ''}"
        elif days >= 60:
            months = days // 30
            return f"{months} month{'s' if months != 1 else ''}"
        elif days >= 30:
            return "1 month"
        elif days >= 7:
            weeks = days // 7
            return f"{weeks} week{'s' if weeks != 1 else ''}"
        elif days > 0:
            return f"{days} day{'s' if days != 1 else ''}"
        elif hours > 0:
            return f"{hours} hour{'s' if hours != 1 else ''}"
        elif minutes > 0:
            return f"{minutes} minute{'s' if minutes != 1 else ''}"
        else:
            return "just now"
    freshness_text = (
        f"{_elapsed(freshness)} ago"
        if freshness is not None
        else "unknown"
    )
    # Stations get their controlling minor faction as a sub-line under the
    # name; a carrier's vanity name joins the callsign on the title line
    # instead (the game presents them as one name, and no owner Cmdr exists
    # in community data to put below).
    title, owner = escape(s.name), escape(s.owner)
    if s.is_carrier and owner:
        title, owner = f"{title} · {owner}", ""
    header = theme.tooltip_station_header(
        icons.station_icon_html(s, theme.tooltip_icon_px()),
        title,
        f"Market data from: {escape(freshness_text)}",
        escape(s.system),
        f"{kind} · {escape(s.station_type or 'Station')}",
        owner=owner,
    )
    return f"{header}{theme.tooltip_stock_table(stock, missing)}{hint}"


def _format_ls(ls: float) -> str:
    """Compact supercruise arrival distance, e.g. 146, 2.3k, 58k Ls."""
    if ls >= 1000:
        return f"{ls / 1000:,.1f}k"
    return f"{ls:,.0f}"

