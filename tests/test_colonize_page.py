"""Tests for the colonisation-target search page."""

# SPDX-License-Identifier: GPL-3.0-or-later

from types import SimpleNamespace

import pytest
from PySide6.QtCore import QThreadPool
from PySide6.QtTest import QTest

from edsc.config import Config
from edsc.gui.colonize_page import _REFILTER_DEBOUNCE_MS, ColonizePage
from edsc.systems import (
    AgentStation,
    ColonizeSearch,
    FilterResult,
    SystemFilters,
    SystemResult,
)
from tests.fakes import FakeSearches

# Long enough for the debounce to have fired and been processed.
_PAST_DEBOUNCE_MS = _REFILTER_DEBOUNCE_MS + 150


@pytest.fixture
def searches():
    return FakeSearches()


@pytest.fixture
def refilters():
    return FakeSearches()


@pytest.fixture
def make_page(qapp, searches, refilters):
    built = []

    def build(**config_kwargs):
        config = Config(**config_kwargs)
        widget = ColonizePage(
            config,
            QThreadPool(),
            task_factory=searches,
            filter_task_factory=refilters,
        )
        built.append(widget)
        return widget

    yield build
    for widget in built:
        widget.stop()
        widget.deleteLater()
    qapp.processEvents()


@pytest.fixture
def page(make_page):
    return make_page()


def _state(system="Sol"):
    return SimpleNamespace(current_system=system)


def _candidate(name="Cand", distance=10.0, steps=1, agent_dist=12.0, **overrides):
    agent = AgentStation("Hub", "Pop", agent_dist) if agent_dist is not None else None
    values = {
        "name": name,
        "id64": abs(hash(name)) % 1_000_000,
        "distance_ly": distance,
        "body_count": 8,
        "steps": steps,
        "agent": agent,
    }
    values.update(overrides)
    return SystemResult(**values)


def _search_and_return(page, searches, search, state=None):
    """Drive one full search: first view -> Spansh answers -> table filled."""
    page.render(state or _state())
    searches.last.done(search)


def _names(model):
    return {model.row_at(row).name for row in range(model.rowCount())}


#  auto-search contract


def test_first_view_searches_once_with_the_deck_settings(make_page, searches):
    page = make_page(
        colonize_range_ly=42, colonize_body_types=["ELW"], colonize_body_weight=2.0
    )
    page.render(_state())

    assert searches.count == 1
    reference, range_ly = searches.last.args
    assert reference == "Sol"
    assert range_ly == 42
    assert searches.last.kwargs["filters"].body_types == ("ELW",)
    assert searches.last.kwargs["sort"] == "balanced"
    assert searches.last.kwargs["body_weight"] == 2.0
    assert "within 42 Ly of Sol" in page.status.text()


def test_spansh_is_queried_only_once_per_session(page, searches):
    _search_and_return(page, searches, ColonizeSearch(results=[_candidate()], reachable=1))

    page.render(_state())
    page.render(_state())

    assert searches.count == 1


def test_an_unknown_location_waits_instead_of_searching(page, searches):
    page.render(_state(system=""))

    assert searches.count == 0
    assert "Waiting for your location" in page.status.text()
    assert page.model.rowCount() == 0


def test_the_decks_search_button_runs_a_new_search(page, searches):
    _search_and_return(page, searches, ColonizeSearch(results=[_candidate()], reachable=1))

    page.filters.searchRequested.emit()

    assert searches.count == 2


def test_a_failed_search_surfaces_the_error_and_re_enables_the_deck(page, searches):
    page.render(_state())
    assert not page.filters.isEnabled()  # disabled while in flight

    searches.last.fail("spansh is down")

    assert "Search failed: spansh is down" in page.status.text()
    assert page.filters.isEnabled()
    assert not page.busy


def test_stop_cancels_an_in_flight_search(page, searches):
    page.render(_state())

    page.stop()

    assert searches.last.cancelled is True


def test_stop_cancels_an_in_flight_refilter(page, searches, refilters):
    """A re-filter hits the network for rings and agents, so it can hang too."""
    _searched_with_pool(page, searches)
    page.filters.changed.emit()
    QTest.qWait(_PAST_DEBOUNCE_MS)
    assert refilters.count == 1

    page.stop()

    assert refilters.last.cancelled is True


def test_stop_drops_a_refilter_that_has_not_started_yet(page, searches, refilters):
    """The debounce must not fire a fresh task into a torn-down page."""
    _searched_with_pool(page, searches)
    page.filters.changed.emit()  # debounce running; no task yet

    page.stop()
    QTest.qWait(_PAST_DEBOUNCE_MS)

    assert refilters.count == 0


#  results


def test_completion_reports_claimable_truncation_and_agent_failures(page, searches):
    search = ColonizeSearch(
        results=[
            _candidate(name="Near", steps=1, agent_dist=12.0),
            _candidate(name="Far", steps=2, agent_dist=None, agent_error=True),
        ],
        total_in_range=9,
        reachable=4,
        graph_truncated=True,
        covered_ly=12.0,  # the row ceiling cut the 20 Ly radius short
    )
    page.filters.set_range_ly(20)
    _search_and_return(page, searches, search)

    assert page.model.rowCount() == 2
    status = page.status.text()
    # A truncated search reports how far it actually reached, never the radius asked for -- it knows nothing about systems past 12 Ly.
    assert "Top 2 of 4 reachable systems within the nearest 12 Ly" in status
    assert "20 Ly" not in status
    assert "1 claimable now" in status
    assert "data ≤7d old" in status
    assert "at most 10,000 systems" in status
    assert "1 agent lookups failed" in status


def test_a_search_that_reached_its_radius_quotes_the_radius(page, searches):
    page.filters.set_range_ly(20)
    _search_and_return(
        page,
        searches,
        ColonizeSearch(results=[_candidate()], reachable=1, covered_ly=20.0),
    )

    assert "within 20 Ly" in page.status.text()


def test_empty_results_suggest_widening_the_range(page, searches):
    """With default filters there is nothing to loosen, so the radius is the ask."""
    _search_and_return(page, searches, ColonizeSearch(results=[], reachable=0))

    assert page.model.rowCount() == 0
    assert "widen the range" in page.status.text()


def test_empty_results_under_active_filters_suggest_loosening_them(
    make_page, searches
):
    page = make_page(colonize_body_types=["ELW"])
    _search_and_return(page, searches, ColonizeSearch(results=[], reachable=0))

    assert "loosen the filters" in page.status.text()


def test_a_superseded_search_result_is_dropped(page, searches):
    page.render(_state())
    first = searches.last
    page.refresh()
    searches.last.done(ColonizeSearch(results=[_candidate(name="New")], reachable=1))

    first.done(ColonizeSearch(results=[_candidate(name="Old")], reachable=1))

    assert _names(page.model) == {"New"}


#  staleness hints


def test_jumping_away_flags_the_results_as_anchored_elsewhere(page, searches):
    _search_and_return(page, searches, ColonizeSearch(results=[_candidate()], reachable=1))

    page.render(_state(system="Wolf 359"))

    assert "results from Sol (↻ to update)" in page.ref_label.toolTip()
    assert searches.count == 1  # flagged, not re-searched


def test_a_changed_radius_flags_the_results_without_searching(page, searches):
    page.filters.set_range_ly(20)
    _search_and_return(page, searches, ColonizeSearch(results=[_candidate()], reachable=1))

    page.filters.set_range_ly(50)
    page.filters.rangeEdited.emit()
    page.render(_state())

    assert "range changed (↻ to update)" in page.ref_label.toolTip()
    assert searches.count == 1


def test_moving_the_radius_slider_never_searches(page, searches):
    _search_and_return(page, searches, ColonizeSearch(results=[_candidate()], reachable=1))

    page.filters.set_range_ly(120)
    page.filters.rangeEdited.emit()

    assert searches.count == 1
    assert page.filters.range_ly() == 120


#  live re-filtering of the cached pool


def _searched_with_pool(page, searches, pool=None):
    """Search, then cache a pool wider than the page actually displays."""
    pool = pool or [_candidate(name=n) for n in ("A", "B", "C")]
    page.filters.set_range_ly(40)
    _search_and_return(
        page, searches, ColonizeSearch(results=pool[:1], reachable=12, pool=pool)
    )
    return pool


def test_a_burst_of_filter_changes_coalesces_into_one_refilter(
    page, searches, refilters
):
    _searched_with_pool(page, searches)

    for _ in range(5):
        page.filters.changed.emit()
    assert refilters.count == 0  # debounced: nothing has run yet

    QTest.qWait(_PAST_DEBOUNCE_MS)

    assert refilters.count == 1  # five changes, one re-filter
    assert searches.count == 1  # and no new network search


def test_a_refilter_carries_the_cached_pool_and_current_filters(
    make_page, searches, refilters
):
    page = make_page(colonize_body_types=["ELW"], colonize_body_weight=3.0)
    pool = _searched_with_pool(page, searches)
    assert page.model.rowCount() < len(pool)  # only the top slice is on screen

    page.filters.changed.emit()
    QTest.qWait(_PAST_DEBOUNCE_MS)

    # The whole reachable pool is re-sliced, not just the rows in view.
    candidates, filters = refilters.last.args
    assert [c.name for c in candidates] == [c.name for c in pool]
    assert filters.body_types == ("ELW",)
    assert refilters.last.kwargs == {"sort": "balanced", "body_weight": 3.0}


def test_filter_changes_before_any_search_are_ignored(page, refilters):
    """Nothing is cached yet; the next ↻ Search will apply the filters."""
    page.filters.changed.emit()
    QTest.qWait(_PAST_DEBOUNCE_MS)

    assert refilters.count == 0


def test_a_fresh_search_supersedes_a_pending_refilter(page, searches, refilters):
    _searched_with_pool(page, searches)

    page.filters.changed.emit()
    page.refresh()  # a full search will render with these filters anyway
    QTest.qWait(_PAST_DEBOUNCE_MS)

    assert refilters.count == 0
    assert searches.count == 2


def test_a_refilter_result_updates_the_rows_and_status(page, searches, refilters):
    _searched_with_pool(page, searches)
    page.filters.changed.emit()
    QTest.qWait(_PAST_DEBOUNCE_MS)

    refilters.last.done(
        FilterResult(results=[_candidate(name="Near", agent_dist=10.0)], matched=3)
    )

    assert _names(page.model) == {"Near"}
    status = page.status.text()
    assert "1 of 3 matching" in status
    assert "12 reachable within 40 Ly" in status
    assert "1 claimable now" in status


def test_a_refilter_that_matches_nothing_prompts_to_loosen(page, searches, refilters):
    _searched_with_pool(page, searches)
    page.filters.changed.emit()
    QTest.qWait(_PAST_DEBOUNCE_MS)

    refilters.last.done(FilterResult(results=[], matched=0))

    assert page.model.rowCount() == 0
    assert "None of 12 reachable systems within 40 Ly" in page.status.text()
    assert "loosen them" in page.status.text()


def test_a_refilter_flags_a_ring_lookup_failure(page, searches, refilters):
    _searched_with_pool(page, searches)
    page.filters.changed.emit()
    QTest.qWait(_PAST_DEBOUNCE_MS)

    refilters.last.done(
        FilterResult(results=[_candidate(name="X")], matched=1, ring_error=True)
    )

    assert "ring lookup failed" in page.status.text()


def test_a_superseded_refilter_result_is_dropped(page, searches, refilters):
    _searched_with_pool(page, searches)
    page.filters.changed.emit()
    QTest.qWait(_PAST_DEBOUNCE_MS)
    first = refilters.last

    page.filters.changed.emit()  # a second re-filter overtakes it
    QTest.qWait(_PAST_DEBOUNCE_MS)
    refilters.last.done(FilterResult(results=[_candidate(name="New")], matched=1))
    first.done(FilterResult(results=[_candidate(name="Old")], matched=1))

    assert _names(page.model) == {"New"}


def test_a_failed_refilter_surfaces_the_error(page, searches, refilters):
    _searched_with_pool(page, searches)
    page.filters.changed.emit()
    QTest.qWait(_PAST_DEBOUNCE_MS)

    refilters.last.fail("rings unavailable")

    assert "Filter failed: rings unavailable" in page.status.text()


def test_settings_changes_reach_the_filter_deck(page):
    page.config.colonize_range_ly = 99

    page.sync_from_config()

    assert page.filters.range_ly() == 99


def test_the_default_deck_reports_no_active_filters(page):
    """The 'loosen vs widen' hint depends on this being an exact match."""
    assert page.filters.system_filters() == SystemFilters()
