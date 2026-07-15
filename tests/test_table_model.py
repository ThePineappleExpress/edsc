import os
import re
from datetime import datetime, timedelta, timezone

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import Qt
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QApplication

from edsc.gui import theme
from edsc.gui.table_model import (
    ST_COVER_COL,
    ST_MATCH_COL,
    ST_SYSTEM_COL,
    CommodityTableModel,
    StationTableModel,
    formatted_date,
)
from edsc.model import CommodityRow
from edsc.stations import StationResult

# Icon rendering (QPixmap/QPainter) needs a Qt application instance.
_app = QApplication.instance() or QApplication([])


def _row(key, *, done=False):
    provided = 10 if done else 0
    remaining = 0 if done else 10
    return CommodityRow(key=key, name=key.title(), required=10, provided=provided,
                        in_cargo=0, remaining=remaining, short=remaining)


def test_station_stock_highlights_name_column():
    m = CommodityTableModel()
    m.set_rows([_row("steel"), _row("aluminium")])
    m.set_station_stock({"steel"})

    assert m._foreground(m.row_at(0), 0) == theme.READY
    assert m._foreground(m.row_at(1), 0) == theme.ORANGE
    # Only the name column is highlighted; numbers keep their own colours.
    assert m._foreground(m.row_at(0), 1) == theme.ORANGE


def test_done_rows_keep_done_colour_over_highlight():
    m = CommodityTableModel()
    m.set_rows([_row("steel", done=True)])
    m.set_station_stock({"steel"})
    assert m._foreground(m.row_at(0), 0) == theme.DONE


def test_clearing_station_stock_removes_highlight():
    m = CommodityTableModel()
    m.set_rows([_row("steel")])
    m.set_station_stock({"steel"})
    m.set_station_stock(set())
    assert m._foreground(m.row_at(0), 0) == theme.ORANGE


def _station(**overrides):
    kwargs = dict(
        name="Jameson Memorial", system="Shinrarta Dezhra", distance_ly=12.3,
        arrival_ls=325.0, has_large_pad=True, is_planetary=False,
        station_type="Coriolis Starport", is_carrier=False,
        market_updated_at="2026-07-01", matched=["steel", "aluminium"],
        missing=["copper", "titanium"],
        needed_total=4, covered_tons=50, demand_tons=100,
        supply_by_name={"steel": 100, "aluminium": 20, "copper": 10},
        demand_by_name={"steel": 100, "aluminium": 40, "copper": 50, "titanium": 25},
    )
    kwargs.update(overrides)
    return StationResult(**kwargs)


def test_station_match_and_coverage_are_separate_columns():
    m = StationTableModel()
    m.set_rows([_station()])
    match = m.data(m.index(0, ST_MATCH_COL), Qt.DisplayRole)
    cover = m.data(m.index(0, ST_COVER_COL), Qt.DisplayRole)
    assert match == "2/4"
    assert cover == "50%"


def test_station_tooltip_splits_stock_and_missing_columns():
    m = StationTableModel()
    m.set_rows([_station()])
    tip = m.data(m.index(0, 0), Qt.ToolTipRole)
    # Borderless rich-text table: fully stocked entries green, partially
    # stocked entries yellow, with their stocked share of the requirement
    # right-aligned beside them in the same colour; missing entries red
    # names only.
    assert ">Stock</th>" in tip and ">Missing</th>" in tip
    assert f"color: {theme.DONE.name()}'>steel</td>" in tip
    assert f"color: {theme.READY.name()}'>aluminium</td>" in tip
    assert f"color: {theme.SHORT.name()}'>copper</td>" in tip
    assert f"color: {theme.DONE.name()}'>100%</td>" in tip  # steel: 100 of 100
    assert f"color: {theme.READY.name()}'>50%</td>" in tip  # aluminium: 20 of 40
    assert "20%" not in tip     # missing entries carry no percentage
    # No dividing line anywhere (checked outside the base64 icon payload).
    assert "border" not in re.sub(r"<img[^>]*>", "", tip)
    # The copy hint belongs to the System column only.
    assert "Click to copy" not in tip


def test_station_tooltip_header_styles_name_system_and_icon():
    m = StationTableModel()
    m.set_rows([_station()])
    tip = m.data(m.index(0, 0), Qt.ToolTipRole)
    # Port and system names sit on separate, individually styled lines,
    # with the type sprite embedded beside the port name.
    assert "Jameson Memorial - Shinrarta Dezhra" not in tip
    assert f"color: {theme.ORANGE_DIM.name()}" in tip and "font-weight: bold" in tip
    assert f"color: {theme.ORANGE.name()}" in tip
    assert ">Shinrarta Dezhra</span>" in tip
    assert "<img src='data:image/png;base64," in tip
    # Freshness tops the tooltip, above the port name and the system name.
    assert (
        tip.index("Market data from:")
        < tip.index("Jameson Memorial")
        < tip.index("Shinrarta Dezhra")
    )


def test_station_tooltip_shows_owner_under_the_name():
    m = StationTableModel()
    m.set_rows([_station(owner="Hutton Orbital Truckers Co-Operative")])
    tip = m.data(m.index(0, 0), Qt.ToolTipRole)
    # The faction/owner line sits in the name cell, right under the name.
    assert "Hutton Orbital Truckers Co-Operative</span></td>" in tip
    assert tip.index("Jameson Memorial") < tip.index("Hutton Orbital")


def test_station_tooltip_omits_unknown_owner():
    m = StationTableModel()
    m.set_rows([_station(owner="")])
    tip = m.data(m.index(0, 0), Qt.ToolTipRole)
    # No stray empty line in the name cell when Spansh has no owner data.
    assert "Jameson Memorial</span></td>" in tip


def test_carrier_tooltip_joins_vanity_name_to_the_callsign():
    m = StationTableModel()
    m.set_rows([
        _station(
            name="T9Z-94L",
            station_type="Drake-Class Carrier",
            is_carrier=True,
            owner="PEQUOD",
        )
    ])
    tip = m.data(m.index(0, 0), Qt.ToolTipRole)
    # One title line in the callsign's own formatting, not a sub-line.
    assert "T9Z-94L · PEQUOD</span></td>" in tip


def test_carrier_row_joins_vanity_name_to_the_callsign():
    m = StationTableModel()
    m.set_rows([
        _station(
            name="T9Z-94L",
            station_type="Drake-Class Carrier",
            is_carrier=True,
            owner="PEQUOD",
        ),
        _station(
            name="H4X-0FF",
            station_type="Drake-Class Carrier",
            is_carrier=True,
            owner="",
        ),
    ])
    # The Station column shows the vanity name beside the registration, as
    # the tooltip title does; without one the callsign stands alone.
    assert m.data(m.index(0, 0), Qt.DisplayRole) == "T9Z-94L · PEQUOD"
    assert m.data(m.index(1, 0), Qt.DisplayRole) == "H4X-0FF"


def test_station_row_never_joins_the_faction_to_the_name():
    m = StationTableModel()
    m.set_rows([_station(owner="Hutton Orbital Truckers Co-Operative")])
    # Only carriers get the joined title; a station's minor faction stays
    # in the tooltip sub-line.
    assert m.data(m.index(0, 0), Qt.DisplayRole) == "Jameson Memorial"


def test_station_rows_show_type_icon_instead_of_marker():
    m = StationTableModel()
    m.set_rows([_station()])
    icon = m.data(m.index(0, 0), Qt.DecorationRole)
    assert isinstance(icon, QIcon) and not icon.isNull()
    assert m.data(m.index(0, ST_SYSTEM_COL), Qt.DecorationRole) is None
    # The old text markers are gone from the station name.
    assert m.data(m.index(0, 0), Qt.DisplayRole) == "Jameson Memorial"


def test_station_tooltip_system_column_adds_copy_hint():
    m = StationTableModel()
    m.set_rows([_station()])
    tip = m.data(m.index(0, ST_SYSTEM_COL), Qt.ToolTipRole)
    assert "Click to copy the system name" in tip


def test_station_tooltip_full_match_omits_the_missing_column():
    m = StationTableModel()
    m.set_rows([_station(missing=[])])
    tip = m.data(m.index(0, 0), Qt.ToolTipRole)
    assert ">Stock</th>" in tip
    assert "Missing" not in tip


def test_spansh_timestamp_with_short_utc_offset_is_parsed():
    assert formatted_date("2025-12-25 06:32:13+00") == datetime(
        2025, 12, 25, 6, 32, 13, tzinfo=timezone.utc
    )

    m = StationTableModel()
    m.set_rows([_station(market_updated_at="2025-12-25 06:32:13+00")])
    tip = m.data(m.index(0, 0), Qt.ToolTipRole)
    assert "Market data from:" in tip and "ago" in tip


def test_spansh_timestamp_variants_are_parsed_as_utc():
    expected = datetime(2026, 7, 1, tzinfo=timezone.utc)
    assert formatted_date("2026-07-01T00:00:00Z") == expected
    assert formatted_date("2026-07-01") == expected
    assert formatted_date("2026-07-01T02:00:00+02:00") == expected


def test_station_tooltip_freshness_scales_to_months_and_years():
    def tip_for(days):
        stamp = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
        m = StationTableModel()
        m.set_rows([_station(market_updated_at=stamp)])
        return m.data(m.index(0, 0), Qt.ToolTipRole)

    assert "3 months ago" in tip_for(100)
    assert "1 year ago" in tip_for(400)


def test_station_tooltip_falls_back_for_invalid_market_timestamp():
    m = StationTableModel()
    m.set_rows([_station(market_updated_at="not-a-date")])
    tip = m.data(m.index(0, 0), Qt.ToolTipRole)
    assert "Market data from: unknown" in tip


def test_station_coverage_colours_both_split_columns():
    m = StationTableModel()
    m.set_rows([_station()])
    s = m.row_at(0)
    assert m._foreground(s, 0) == theme.ORANGE
    # 50% coverage -> READY on both halves of the old combined column.
    assert m._foreground(s, ST_MATCH_COL) == theme.READY
    assert m._foreground(s, ST_COVER_COL) == theme.READY

    full = _station(covered_tons=100)
    assert m._foreground(full, ST_MATCH_COL) == theme.DONE
    low = _station(covered_tons=10)
    assert m._foreground(low, ST_COVER_COL) == theme.SHORT
