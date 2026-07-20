"""Tests for the nearest-stations search page."""

# SPDX-License-Identifier: GPL-3.0-or-later

from types import SimpleNamespace

import pytest
from PySide6.QtCore import QThreadPool
from PySide6.QtGui import QGuiApplication
from PySide6.QtTest import QTest

from edsc.config import Config
from edsc.gui.stations_page import StationsPage
from edsc.gui.table_model import ST_DIST_COL, ST_SYSTEM_COL
from edsc.stations import StationResult
from tests.fakes import FakeSearches

NEEDS = {"Steel": 100, "Aluminium": 500}


@pytest.fixture
def searches():
    return FakeSearches()


@pytest.fixture
def page(qapp, searches):
    config = Config(
        stations_include_planets=False,
        stations_include_carriers=False,
    )
    pool = QThreadPool()
    widget = StationsPage(config, pool, task_factory=searches)
    yield widget
    widget.stop()
    widget.deleteLater()
    qapp.processEvents()


def _state(system="Sol", needs=None):
    needs = NEEDS if needs is None else needs
    return SimpleNamespace(current_system=system, outstanding_needs=lambda: needs)


def _station(name="Primary", system="Sol", **overrides):
    values = {
        "name": name,
        "system": system,
        "distance_ly": 1.0,
        "arrival_ls": 100.0,
        "has_large_pad": True,
        "is_planetary": False,
        "station_type": "Coriolis Starport",
        "is_carrier": False,
        "market_updated_at": "2026-07-11T08:00:00Z",
        "matched": ["Steel"],
        "missing": ["Aluminium"],
        "needed_total": 2,
        "covered_tons": 100,
        "demand_tons": 100,
        "supply_by_name": {"Steel": 100},
        "demand_by_name": {"Steel": 100, "Aluminium": 500},
    }
    values.update(overrides)
    return StationResult(**values)


def _planetary(name="Surface", **overrides):
    return _station(
        name=name, is_planetary=True, station_type="Planetary Port", **overrides
    )


def _carrier(name="K1T-00A", **overrides):
    return _station(
        name=name, is_carrier=True, station_type="Drake-Class Carrier", **overrides
    )


def _search_and_return(page, searches, results, state=None):
    """Drive one full search: first view -> Spansh answers -> tables filled."""
    page.render(state or _state())
    searches.last.done(results)


def _names(model):
    return {model.row_at(row).name for row in range(model.rowCount())}


#  auto-search contract


def test_first_view_searches_once_and_fills_the_table(page, searches):
    page.render(_state())

    assert searches.count == 1
    reference, needs, recent_only = searches.last.args
    assert reference == "Sol"
    assert needs == NEEDS
    assert recent_only is False
    assert searches.last.kwargs == {"range_ly": 0, "sort": "match"}
    assert "Searching Spansh near Sol" in page.status.text()

    searches.last.done([_station()])
    assert _names(page.model) == {"Primary"}


def test_spansh_is_queried_only_once_per_session(page, searches):
    _search_and_return(page, searches, [_station()])

    page.render(_state())
    page.render(_state())

    assert searches.count == 1


def test_a_known_location_with_nothing_outstanding_never_searches(page, searches):
    page.render(_state(needs={}))

    assert searches.count == 0
    assert "Nothing outstanding" in page.status.text()
    assert page.model.rowCount() == 0


def test_an_unknown_location_waits_instead_of_searching(page, searches):
    page.render(_state(system=""))

    assert searches.count == 0
    assert "Waiting for your location" in page.status.text()
    assert page.model.rowCount() == 0


def test_explicit_refresh_runs_a_new_search(page, searches):
    _search_and_return(page, searches, [_station()])

    page.refresh_btn.click()

    assert searches.count == 2


def test_recent_toggle_reruns_the_search_both_ways(page, searches):
    _search_and_return(page, searches, [_station()])

    page.recent_btn.setChecked(True)
    assert searches.count == 2
    assert searches.last.args[2] is True  # recent_only reached the query
    assert page.config.stations_recent_only is True

    page.recent_btn.setChecked(False)
    assert searches.count == 3
    assert searches.last.args[2] is False
    assert page.config.stations_recent_only is False


#  results and staleness


def test_search_completion_counts_every_cached_category(page, searches):
    """The counts describe the whole fetched pool, not the filtered view."""
    _search_and_return(page, searches, [_station(), _planetary(), _carrier()])

    assert "Cached 1 orbital · 1 planetary · 1 carriers" in page.status.text()


def test_a_recent_only_search_says_so_in_the_status(page, searches):
    _search_and_return(page, searches, [_station()])

    page.recent_btn.setChecked(True)  # runs a fresh search
    searches.last.done([_station()])

    assert "≤24h old" in page.status.text()


def test_no_results_says_so_without_clearing_the_controls(page, searches):
    page.render(_state())
    searches.last.done([])

    assert page.model.rowCount() == 0
    assert "No stations found" in page.status.text()
    assert page.refresh_btn.isEnabled()


def test_a_failed_search_surfaces_the_error_and_re_enables_search(page, searches):
    page.render(_state())
    assert not page.refresh_btn.isEnabled()  # disabled while in flight

    searches.last.fail("spansh is down")

    assert "Search failed: spansh is down" in page.status.text()
    assert page.refresh_btn.isEnabled()
    assert not page.busy


def test_a_superseded_search_result_is_dropped(page, searches):
    """A slow first search must not overwrite a newer one that already landed."""
    page.render(_state())
    first = searches.last
    page.refresh()  # the button is disabled mid-search; the hotkey path is not
    second = searches.last

    second.done([_station(name="New")])
    first.done([_station(name="Old")])  # lands late, out of order

    assert _names(page.model) == {"New"}


def test_a_failure_from_a_superseded_search_is_dropped(page, searches):
    page.render(_state())
    first = searches.last
    page.refresh()
    searches.last.done([_station(name="New")])

    first.fail("timed out")

    assert "Search failed" not in page.status.text()
    assert _names(page.model) == {"New"}


def test_jumping_away_flags_the_results_as_anchored_elsewhere(page, searches):
    _search_and_return(page, searches, [_station()])

    page.render(_state(system="Wolf 359"))

    assert "results from Sol (↻ to update)" in page.ref_label.toolTip()
    assert searches.count == 1  # flagged, not re-searched


def test_changed_demand_flags_the_results_as_stale(page, searches):
    _search_and_return(page, searches, [_station()])

    page.render(_state(needs={"Steel": 100}))

    assert "demand changed (↻ to update)" in page.ref_label.toolTip()
    assert searches.count == 1


def test_stop_cancels_an_in_flight_search(page, searches):
    page.render(_state())

    page.stop()

    assert searches.last.cancelled is True


#  local filtering of the cached pool


def test_primary_filter_toggles_use_cached_data_without_searching(page, searches):
    orbitals = [_station(name=f"Orbital {i}", distance_ly=float(i + 1)) for i in range(10)]
    planetaries = [
        _planetary(name=f"Planetary {i}", distance_ly=float(i + 11)) for i in range(10)
    ]
    carriers = [_carrier(name=f"Carrier {i}", distance_ly=float(i + 21)) for i in range(10)]
    _search_and_return(page, searches, [*orbitals, *planetaries, *carriers])

    def has(prefix):
        return any(name.startswith(prefix) for name in _names(page.model))

    # Both disabled means orbital-only.
    assert page.model.rowCount() == 10
    assert _names(page.model) == {s.name for s in orbitals}

    # Each checked category is represented alongside orbitals, while the final mixed list stays capped at ten rows.
    page.planets_btn.setChecked(True)
    assert page.model.rowCount() == 10
    assert has("Orbital") and has("Planetary")

    page.carriers_btn.setChecked(True)
    assert page.model.rowCount() == 10
    assert has("Orbital") and has("Planetary") and has("Carrier")

    page.planets_btn.setChecked(False)
    assert page.model.rowCount() == 10
    assert has("Orbital") and has("Carrier") and not has("Planetary")

    page.carriers_btn.setChecked(False)
    assert _names(page.model) == {s.name for s in orbitals}

    assert searches.count == 1  # every toggle re-sliced the cache, no I/O


def test_supplementary_filters_cover_what_the_best_station_misses(page, searches):
    orbital = _station()
    planetary = _planetary(
        name="Surface Aluminium",
        matched=["Aluminium"],
        missing=["Steel"],
        supply_by_name={"Aluminium": 500},
        distance_ly=2.0,
    )
    carrier = _carrier(
        matched=["Aluminium"],
        missing=["Steel"],
        supply_by_name={"Aluminium": 500},
        distance_ly=3.0,
    )
    page.planets_btn2.setChecked(True)
    page.carriers_btn2.setChecked(True)
    _search_and_return(page, searches, [orbital, planetary, carrier])

    assert _names(page.model2) == {"Surface Aluminium", "K1T-00A"}

    page.planets_btn2.setChecked(False)
    assert page.model2.row_at(0) is carrier
    page.carriers_btn2.setChecked(False)
    assert page.model2.rowCount() == 0
    page.planets_btn2.setChecked(True)
    assert page.model2.row_at(0) is planetary

    assert searches.count == 1
    assert not page.more_bar.isHidden()
    assert "hidden by the supplementary filters" not in page.status.text()


def test_supplementary_filters_never_touch_the_primary_config(page):
    page.planets_btn2.setChecked(True)
    page.carriers_btn2.setChecked(True)

    assert page.config.stations_include_planets is False
    assert page.config.stations_include_carriers is False


def test_an_empty_follow_up_keeps_its_filter_controls_available(page, searches):
    """The residual demand is unmet, so the broadening filters must stay put."""
    _search_and_return(page, searches, [_station()])

    assert not page.more_bar.isHidden()
    assert not page.table2.isHidden()
    assert page.model2.rowCount() == 0
    assert page.more_label.text().startswith("No cached stations")


def test_filtering_everything_out_prompts_to_broaden_or_research(page, searches):
    _search_and_return(page, searches, [_planetary()])  # planets are off

    assert page.model.rowCount() == 0
    assert "No cached stations match the primary filters" in page.status.text()


def test_one_station_covering_everything_reports_a_single_stop(page, searches):
    complete = _station(
        matched=["Steel", "Aluminium"],
        missing=[],
        supply_by_name={"Steel": 100, "Aluminium": 500},
    )
    _search_and_return(page, searches, [complete])

    assert "complete in one stop" in page.status.text()


#  click to copy


def test_clicking_a_system_cell_copies_it_ready_to_paste_in_game(page, searches):
    _search_and_return(page, searches, [_station(system="Wolf 359")])

    page.table.clicked.emit(page.model.index(0, ST_SYSTEM_COL))

    assert QGuiApplication.clipboard().text() == "Wolf 359"
    assert "Copied 'Wolf 359' to clipboard" in page.status.text()


def test_the_copy_notice_gives_the_status_line_back(page, searches, qapp, monkeypatch):
    from edsc.gui import widgets

    monkeypatch.setattr(widgets, "_FLASH_MS", 50)  # not 2.5 s of real waiting
    _search_and_return(page, searches, [_station(system="Wolf 359")])
    searched = page.status.text()

    page.table.clicked.emit(page.model.index(0, ST_SYSTEM_COL))
    QTest.qWait(250)

    assert page.status.text() == searched


def test_clicking_any_other_column_copies_nothing(page, searches):
    QGuiApplication.clipboard().setText("untouched")
    _search_and_return(page, searches, [_station(system="Wolf 359")])

    page.table.clicked.emit(page.model.index(0, ST_DIST_COL))

    assert QGuiApplication.clipboard().text() == "untouched"


def test_clicking_the_follow_up_table_copies_from_that_table(page, searches):
    """Both tables share one handler; it must read the one that was clicked."""
    page.planets_btn2.setChecked(True)
    _search_and_return(
        page,
        searches,
        [
            _station(system="Primary System"),
            _planetary(
                name="Surface Aluminium",
                system="Follow Up System",
                matched=["Aluminium"],
                missing=["Steel"],
                supply_by_name={"Aluminium": 500},
            ),
        ],
    )

    page.table2.clicked.emit(page.model2.index(0, ST_SYSTEM_COL))

    assert QGuiApplication.clipboard().text() == "Follow Up System"


def test_settings_changes_are_mirrored_without_starting_a_search(page, searches):
    _search_and_return(page, searches, [_station()])
    page.config.stations_include_planets = True
    page.config.stations_recent_only = True

    page.sync_from_config()

    assert page.planets_btn.isChecked()
    assert page.planets_btn2.isChecked()
    assert page.recent_btn.isChecked()
    assert searches.count == 1  # mirroring must not kick off a search
