import re
from datetime import datetime, timedelta, timezone

from PySide6.QtCore import Qt
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QApplication

from edsc.gui import theme
from edsc.gui.table_model import (
    ST_COVER_COL,
    ST_MATCH_COL,
    ST_SYSTEM_COL,
    SY_AGENT_COL,
    SY_BODIES_COL,
    SY_DIST_COL,
    SY_FURTHEST_COL,
    SY_STARS_COL,
    SY_STEPS_COL,
    SY_SYSTEM_COL,
    CommodityTableModel,
    StationTableModel,
    SystemTableModel,
    formatted_date,
)
from edsc.model import CommodityRow
from edsc.stations import StationResult
from edsc.systems import AgentStation, BodyInfo, SystemResult

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
    kwargs = {
        "name": "Jameson Memorial", "system": "Shinrarta Dezhra", "distance_ly": 12.3,
        "arrival_ls": 325.0, "has_large_pad": True, "is_planetary": False,
        "station_type": "Coriolis Starport", "is_carrier": False,
        "market_updated_at": "2026-07-01", "matched": ["steel", "aluminium"],
        "missing": ["copper", "titanium"],
        "needed_total": 4, "covered_tons": 50, "demand_tons": 100,
        "supply_by_name": {"steel": 100, "aluminium": 20, "copper": 10},
        "demand_by_name": {"steel": 100, "aluminium": 40, "copper": 50, "titanium": 25},
    }
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
    # Borderless rich-text table: fully stocked entries green, partially stocked entries yellow with their stocked share of the requirement right-aligned beside them in the same colour; missing entries red names only.
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
    # Port and system names sit on separate, individually styled lines, with the type sprite embedded beside the port name.
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
    # The Station column shows the vanity name beside the registration, as the tooltip title does; without one the callsign stands alone.
    assert m.data(m.index(0, 0), Qt.DisplayRole) == "T9Z-94L · PEQUOD"
    assert m.data(m.index(1, 0), Qt.DisplayRole) == "H4X-0FF"


def test_station_row_never_joins_the_faction_to_the_name():
    m = StationTableModel()
    m.set_rows([_station(owner="Hutton Orbital Truckers Co-Operative")])
    # Only carriers get the joined title; a station's minor faction stays in the tooltip sub-line.
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


def _candidate(**overrides):
    kwargs = {
        "name": "Lyncis Sector KC-V c2-14",
        "id64": 1,
        "distance_ly": 14.8,
        "body_count": 6,
        "bodies": [
            BodyInfo("A", "Star", "M (Red dwarf) Star", 0.0, True, ""),
            BodyInfo("B", "Star", "M (Red dwarf) Star", 2000.0, False, ""),
            BodyInfo("A 1", "Planet", "Rocky Ice world", 350.0, False,
                     "Terraformable"),
            BodyInfo("A 2", "Planet", "Icy body", 1200.0, False,
                     "Not terraformable"),
        ],
        "nearest_populated_ly": 4.4,
        "updated_at": "2026-07-01 00:00:00+00",
        "steps": 1,
        "agent": AgentStation("Contact Hub", "Dharragense", 12.0),
    }
    kwargs.update(overrides)
    return SystemResult(**kwargs)


def test_system_row_formats_every_column():
    m = SystemTableModel()
    m.set_rows([_candidate()])
    row = [m.data(m.index(0, c), Qt.DisplayRole) for c in range(7)]
    assert row == [
        "Lyncis Sector KC-V c2-14",  # System
        "14.8",                      # Ly
        "1",                         # Steps
        "2",                         # Stars
        "6",                         # Bodies (honk total over 4 scanned)
        "2.0k",                      # Furthest (max distance_to_arrival)
        "Contact Hub · 12.0 Ly",     # Agent
    ]


def test_system_unknowns_render_dim_question_marks():
    m = SystemTableModel()
    m.set_rows([SystemResult("Test")])
    for col in (SY_DIST_COL, SY_STEPS_COL, SY_STARS_COL, SY_BODIES_COL,
                SY_FURTHEST_COL):
        assert m.data(m.index(0, col), Qt.DisplayRole) == "?"
        assert m._foreground(m.row_at(0), col) == theme.TEXT_DIM
    assert m.data(m.index(0, SY_SYSTEM_COL), Qt.DisplayRole) == "Test"


def test_system_agent_cell_states():
    m = SystemTableModel()
    found = _candidate()
    none = _candidate(agent=None)
    error = _candidate(agent=None, agent_error=True)
    m.set_rows([found, none, error])
    assert m.data(m.index(0, SY_AGENT_COL), Qt.DisplayRole) == (
        "Contact Hub · 12.0 Ly"
    )
    assert m.data(m.index(1, SY_AGENT_COL), Qt.DisplayRole) == "none"
    assert m.data(m.index(2, SY_AGENT_COL), Qt.DisplayRole) == "?"


def test_system_steps_colours_scale_with_reachability():
    m = SystemTableModel()
    assert m._foreground(_candidate(steps=1), SY_STEPS_COL) == theme.DONE
    assert m._foreground(_candidate(steps=2), SY_STEPS_COL) == theme.READY
    assert m._foreground(_candidate(steps=3), SY_STEPS_COL) == theme.READY
    assert m._foreground(_candidate(steps=4), SY_STEPS_COL) == theme.ORANGE


def test_system_agent_colours_encode_claimability():
    m = SystemTableModel()
    claimable = _candidate()  # steps 1, agent at 12 Ly
    assert claimable.claimable
    assert m._foreground(claimable, SY_AGENT_COL) == theme.DONE
    far_agent = _candidate(agent=AgentStation("Hub", "Y", 17.0))
    assert m._foreground(far_agent, SY_AGENT_COL) == theme.SHORT
    bridged = _candidate(steps=2)  # agent close, but not claimable yet
    assert m._foreground(bridged, SY_AGENT_COL) == theme.SHORT
    assert m._foreground(_candidate(agent=None), SY_AGENT_COL) == theme.SHORT
    error = _candidate(agent=None, agent_error=True)
    assert m._foreground(error, SY_AGENT_COL) == theme.TEXT_DIM


def test_unverified_system_dims_the_whole_row():
    m = SystemTableModel()
    unverified = _candidate(verified=False)  # steps 1, agent claimable
    # Every populated cell dims, overriding the usual steps/agent semantics.
    for col in (SY_SYSTEM_COL, SY_STEPS_COL, SY_AGENT_COL):
        assert m._foreground(unverified, col) == theme.ORANGE_DIM
    # Unknown cells stay the dimmer grey, not orange-dim.
    bare = _candidate(verified=False, steps=None, agent=None, bodies=[],
                      body_count=None, distance_ly=None)
    assert m._foreground(bare, SY_STARS_COL) == theme.TEXT_DIM


def test_verified_and_unchecked_systems_keep_normal_colours():
    m = SystemTableModel()
    # verified True and the default None both keep the rich reachability colour.
    assert m._foreground(_candidate(verified=True), SY_STEPS_COL) == theme.DONE
    assert m._foreground(_candidate(verified=None), SY_STEPS_COL) == theme.DONE


def test_system_tooltip_reports_verification_state():
    m = SystemTableModel()
    m.set_rows([_candidate(verified=True), _candidate(verified=False),
                _candidate(verified=None)])
    tip_verified = m.data(m.index(0, 0), Qt.ToolTipRole)
    # A confirmed candidate gets Raven's existing asset in the header's top-right corner, with no separate confirmation line consuming space.
    assert "alt='Confirmed by Raven Colonial'" in tip_verified
    assert "rowspan='2' valign='top' align='right'" in tip_verified
    assert "Confirmed on Raven Colonial" not in tip_verified
    # Absence of the corner icon is the only cue an unverified system needs; no line calls it out, so its tooltip stays free of Raven text like the unchecked case below.
    assert "Raven Colonial" not in m.data(m.index(1, 0), Qt.ToolTipRole)
    tip_none = m.data(m.index(2, 0), Qt.ToolTipRole)
    assert "Raven Colonial" not in tip_none


def test_system_tooltip_summarises_bodies_steps_and_agent():
    m = SystemTableModel()
    m.set_rows([_candidate()])
    tip = m.data(m.index(0, SY_DIST_COL), Qt.ToolTipRole)
    assert "Body data from:" in tip and "ago" in tip
    assert "2 stars · 6 bodies · furthest 2.0k Ls" in tip
    assert "Claimable now" in tip
    assert "Contact Hub (Dharragense) at 12.0 Ly - in claim range" in tip
    # Known-body breakdown with subtype counts and completeness fine print.
    assert "M (Red dwarf) Star ×2" in tip
    assert "Rocky Ice world" in tip and "Icy body" in tip
    assert "1 terraformable body" in tip
    assert "4 of 6 bodies scanned" in tip
    # The copy hint belongs to the System column only.
    assert "Click to copy" not in tip
    assert "Click to copy the system name" in m.data(
        m.index(0, SY_SYSTEM_COL), Qt.ToolTipRole
    )


def test_system_tooltip_counts_bridge_colonies():
    m = SystemTableModel()
    m.set_rows([_candidate(steps=3)])
    tip = m.data(m.index(0, 0), Qt.ToolTipRole)
    assert "Needs 2 bridge colonies (16 Ly each)" in tip


def test_system_tooltip_flags_unscanned_systems():
    m = SystemTableModel()
    m.set_rows([_candidate(bodies=[], body_count=None, agent=None)])
    tip = m.data(m.index(0, 0), Qt.ToolTipRole)
    assert "No body data on Spansh" in tip
    assert "No colonisation contact found nearby" in tip
