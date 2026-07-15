import os
from types import SimpleNamespace
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtWidgets import QApplication

from edsc import stations as station_search
from edsc.config import Config
from edsc.gui.overlay import OverlayWindow, _SearchTask
from edsc.stations import StationResult


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance() or QApplication([])
    yield app


@pytest.fixture
def overlay(qapp):
    config = Config(
        stations_include_planets=False,
        stations_include_carriers=False,
    )
    window = OverlayWindow(config)
    window._auto_fit = False
    yield window
    window.stop()
    window.deleteLater()
    qapp.processEvents()


def _station(name="Primary", system="Sol", **overrides):
    values = dict(
        name=name,
        system=system,
        distance_ly=1.0,
        arrival_ls=100.0,
        has_large_pad=True,
        is_planetary=False,
        station_type="Coriolis Starport",
        is_carrier=False,
        market_updated_at="2026-07-11T08:00:00Z",
        matched=["Steel"],
        missing=["Aluminium"],
        needed_total=2,
        covered_tons=100,
        demand_tons=100,
        supply_by_name={"Steel": 100},
        demand_by_name={"Steel": 100, "Aluminium": 500},
    )
    values.update(overrides)
    return StationResult(**values)


def _load_results(overlay, results, needed=None, recent=False):
    needed = needed or {"Steel": 100, "Aluminium": 500}
    overlay._on_search_done(
        overlay._search_seq,
        "Sol",
        needed,
        recent,
        results,
    )


def test_search_task_fetches_exactly_once_with_all_categories():
    results = [_station()]
    with mock.patch.object(
        station_search, "search_stations", return_value=results
    ) as search:
        task = _SearchTask("Sol", {"Steel": 100}, recent_only=True)
        emitted = []
        task.signals.done.connect(emitted.append)
        task.run()

    search.assert_called_once_with(
        "Sol", {"Steel": 100}, recent_only=True
    )
    assert emitted == [results]


def test_primary_filter_toggles_use_cached_data_without_search(overlay):
    orbitals = [
        _station(name=f"Orbital {i}", distance_ly=float(i + 1))
        for i in range(10)
    ]
    planetaries = [
        _station(
            name=f"Planetary {i}",
            is_planetary=True,
            station_type="Planetary Port",
            distance_ly=float(i + 11),
        )
        for i in range(10)
    ]
    carriers = [
        _station(
            name=f"Carrier {i}",
            is_carrier=True,
            station_type="Drake-Class Carrier",
            distance_ly=float(i + 21),
        )
        for i in range(10)
    ]
    _load_results(overlay, [*orbitals, *planetaries, *carriers])

    def displayed_names():
        return {
            overlay.stations_model.row_at(row).name
            for row in range(overlay.stations_model.rowCount())
        }

    with mock.patch.object(overlay, "_start_station_search") as search:
        # Both disabled means orbital-only.
        assert overlay.stations_model.rowCount() == 10
        assert displayed_names() == {station.name for station in orbitals}

        # Each checked category is represented alongside orbitals, while the
        # final mixed list remains capped at ten rows.
        overlay.planets_btn.setChecked(True)
        assert overlay.stations_model.rowCount() == 10
        assert any(name.startswith("Orbital") for name in displayed_names())
        assert any(name.startswith("Planetary") for name in displayed_names())
        overlay.carriers_btn.setChecked(True)
        assert overlay.stations_model.rowCount() == 10
        assert any(name.startswith("Orbital") for name in displayed_names())
        assert any(name.startswith("Planetary") for name in displayed_names())
        assert any(name.startswith("Carrier") for name in displayed_names())

        overlay.planets_btn.setChecked(False)
        assert overlay.stations_model.rowCount() == 10
        assert any(name.startswith("Orbital") for name in displayed_names())
        assert any(name.startswith("Carrier") for name in displayed_names())
        assert not any(name.startswith("Planetary") for name in displayed_names())
        overlay.carriers_btn.setChecked(False)
        assert overlay.stations_model.rowCount() == 10

    search.assert_not_called()
    assert displayed_names() == {station.name for station in orbitals}


def test_supplementary_filters_cover_missing_from_cached_categories(overlay):
    orbital = _station()
    planetary = _station(
        name="Surface Aluminium",
        is_planetary=True,
        station_type="Planetary Port",
        matched=["Aluminium"],
        missing=["Steel"],
        supply_by_name={"Aluminium": 500},
        distance_ly=2.0,
    )
    carrier = _station(
        name="K1T-00A",
        is_carrier=True,
        station_type="Drake-Class Carrier",
        matched=["Aluminium"],
        missing=["Steel"],
        supply_by_name={"Aluminium": 500},
        distance_ly=3.0,
    )
    overlay.planets_btn2.setChecked(True)
    overlay.carriers_btn2.setChecked(True)
    _load_results(overlay, [orbital, planetary, carrier])

    with mock.patch.object(overlay, "_start_station_search") as search:
        assert overlay.stations_model2.rowCount() == 2
        assert {
            overlay.stations_model2.row_at(row).name
            for row in range(overlay.stations_model2.rowCount())
        } == {"Surface Aluminium", "K1T-00A"}

        overlay.planets_btn2.setChecked(False)
        assert overlay.stations_model2.row_at(0) is carrier
        overlay.carriers_btn2.setChecked(False)
        assert overlay.stations_model2.rowCount() == 0
        overlay.planets_btn2.setChecked(True)
        assert overlay.stations_model2.row_at(0) is planetary

    search.assert_not_called()
    assert not overlay.stations_more_bar.isHidden()
    assert "hidden by the supplementary filters" not in overlay.stations_status.text()


def test_empty_local_follow_up_keeps_filter_controls_available(overlay):
    _load_results(overlay, [_station()])

    assert not overlay.stations_more_bar.isHidden()
    assert not overlay.stations_table2.isHidden()
    assert overlay.stations_model2.rowCount() == 0
    assert overlay.stations_more_label.text().startswith("No cached stations")
    assert not hasattr(overlay, "refresh_btn2")


def test_secondary_filters_are_independent_from_primary_config(overlay):
    overlay.planets_btn2.setChecked(True)
    overlay.carriers_btn2.setChecked(True)

    assert overlay.planets_btn2.isChecked()
    assert overlay.carriers_btn2.isChecked()
    assert overlay.config.stations_include_planets is False
    assert overlay.config.stations_include_carriers is False


def test_recent_toggle_retriggers_the_full_search_both_ways(overlay):
    state = SimpleNamespace(
        current_system="Sol",
        outstanding_needs=lambda: {"Steel": 100},
    )
    overlay._state = state

    with mock.patch.object(overlay, "_start_station_search") as search:
        overlay._toggle_recent(True)
        overlay._toggle_recent(False)

    assert search.call_args_list == [mock.call(state), mock.call(state)]
    assert overlay.config.stations_recent_only is False


def test_explicit_refresh_retriggers_search(overlay):
    state = SimpleNamespace(
        current_system="Sol",
        outstanding_needs=lambda: {"Steel": 100},
    )
    overlay._state = state

    with mock.patch.object(overlay, "_start_station_search") as search:
        overlay._refresh_stations()

    search.assert_called_once_with(state)


def test_search_completion_records_category_counts_and_recent_state(overlay):
    orbital = _station()
    planetary = _station(
        name="Surface",
        is_planetary=True,
        station_type="Planetary Port",
    )
    carrier = _station(
        name="K1T-00A",
        is_carrier=True,
        station_type="Drake-Class Carrier",
    )
    overlay.planets_btn.setChecked(True)
    overlay.carriers_btn.setChecked(True)
    _load_results(overlay, [orbital, planetary, carrier], recent=True)

    assert overlay._station_results == [orbital, planetary, carrier]
    assert overlay._search_ref == "Sol"
    assert overlay._search_recent_only is True
    assert "Cached 1 orbital · 1 planetary · 1 carriers" in overlay.stations_status.text()
    assert "≤24h old" in overlay.stations_status.text()
