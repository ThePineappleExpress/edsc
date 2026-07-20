"""The background search tasks: each fetches once and publishes its result."""

from unittest import mock

from edsc import stations as station_search, systems as system_search
from edsc.gui.search_tasks import (
    BackgroundSearchTask,
    ColonizeFilterTask,
    ColonizeSearchTask,
    StationSearchTask,
)
from edsc.systems import ColonizeSearch, FilterResult


def test_station_search_task_fetches_exactly_once_with_all_categories():
    results = [mock.sentinel.station]
    with mock.patch.object(
        station_search, "search_stations", return_value=results
    ) as search:
        task = StationSearchTask("Sol", {"Steel": 100}, recent_only=True)
        emitted = []
        task.signals.done.connect(emitted.append)
        task.run()

    search.assert_called_once_with(
        "Sol", {"Steel": 100}, recent_only=True, range_ly=0, sort="match"
    )
    assert emitted == [results]


def test_colonize_search_task_fetches_exactly_once():
    search = ColonizeSearch(results=[], total_in_range=0, reachable=0)
    with mock.patch.object(
        system_search, "search_colonisation_targets", return_value=search
    ) as fn:
        task = ColonizeSearchTask("Sol", 42)
        emitted = []
        task.signals.done.connect(emitted.append)
        task.run()

    fn.assert_called_once_with("Sol", 42, filters=None, sort="balanced", body_weight=1.0)
    assert emitted == [search]


def test_colonize_filter_task_reslices_the_pool_without_a_search():
    pool = [mock.sentinel.candidate]
    filters = system_search.SystemFilters()
    result = FilterResult(results=[], matched=0)
    with mock.patch.object(
        system_search, "refilter_colonisation", return_value=result
    ) as fn:
        task = ColonizeFilterTask(pool, filters, sort="distance", body_weight=2.0)
        emitted = []
        task.signals.done.connect(emitted.append)
        task.run()

    fn.assert_called_once_with(pool, filters, sort="distance", body_weight=2.0)
    assert emitted == [result]


def test_a_failed_search_surfaces_the_error_instead_of_raising():
    task = BackgroundSearchTask(mock.Mock(side_effect=RuntimeError("spansh is down")))
    errors = []
    task.signals.error.connect(errors.append)

    task.run()

    assert errors == ["spansh is down"]


def test_a_cancelled_task_publishes_nothing():
    """Shutdown cancels in-flight searches; they must not reach dead widgets."""
    task = BackgroundSearchTask(lambda: ["late result"])
    emitted = []
    task.signals.done.connect(emitted.append)
    task.signals.error.connect(emitted.append)

    task.cancel()
    task.run()

    assert emitted == []
