"""Tests for the overlay shell: header, tab bar, and page routing."""

# SPDX-License-Identifier: GPL-3.0-or-later

from unittest import mock

import pytest

from edsc.config import Config
from edsc.gui import icons
from edsc.gui.overlay import OverlayWindow
from edsc.model import (
    COLONIZE_MARKET_ID,
    COMBINED_MARKET_ID,
    STATIONS_MARKET_ID,
    AppState,
    CommodityLine,
    Project,
)


@pytest.fixture
def overlay(qapp):
    window = OverlayWindow(Config())
    window._auto_fit = False
    # Routing keys off the tab bar being visible, so the window has to be up.
    window.show()
    qapp.processEvents()
    # No shell test should ever reach Spansh: a page may build a search task, but nothing will run it.
    with mock.patch.object(window._search_pool, "start"):
        yield window
    window.stop()
    window.deleteLater()
    qapp.processEvents()


def _project(market_id=1, station_name="Construction Site: New Dawn", **overrides):
    values = {
        "market_id": market_id,
        "station_name": station_name,
        "system_name": "Sol",
        "lines": {"steel": CommodityLine(key="steel", required=100, provided=10)},
    }
    values.update(overrides)
    return Project(**values)


def _state(*projects):
    """An app state with no known location, so no page ever starts a search."""
    state = AppState()
    for project in projects:
        state.projects[project.market_id] = project
    return state


def _tab_data(overlay):
    return [overlay.tabs.tabData(i) for i in range(overlay.tabs.count())]


def _tab_texts(overlay):
    return [overlay.tabs.tabText(i) for i in range(overlay.tabs.count())]


def _select(overlay, market_id):
    overlay.tabs.setCurrentIndex(_tab_data(overlay).index(market_id))


#  window chrome


def test_set_opacity_updates_the_panel_without_changing_config(overlay):
    overlay.set_opacity(0.42)

    assert "rgba(0,0,0,107)" in overlay.panel.styleSheet()
    assert overlay.config.overlay_opacity == 0.88


def test_set_status_writes_the_shared_status_line(overlay):
    overlay.set_status("Watching journals")

    assert overlay.status_label.text() == "Watching journals"


#  the tab bar


def test_the_tab_bar_offers_colonize_with_zero_projects(overlay):
    """No construction yet is exactly who browses colonisation targets."""
    overlay.refresh(_state())

    assert not overlay.tabs.isHidden()
    assert _tab_data(overlay) == [COLONIZE_MARKET_ID]


def test_a_single_project_gets_no_combined_tab(overlay):
    """'All' would just duplicate the one construction."""
    overlay.refresh(_state(_project()))

    assert _tab_data(overlay) == [1, STATIONS_MARKET_ID, COLONIZE_MARKET_ID]


def test_two_projects_gain_a_combined_tab_first(overlay):
    overlay.refresh(_state(_project(1), _project(2, station_name="Second")))

    assert _tab_data(overlay) == [
        COMBINED_MARKET_ID,
        1,
        2,
        STATIONS_MARKET_ID,
        COLONIZE_MARKET_ID,
    ]


@pytest.mark.parametrize(
    ("station_name", "expected"),
    [
        ("Orbital Construction Site: New Dawn", "New Dawn"),
        ("Planetary Construction Site: New Dawn", "New Dawn"),
        ("Construction Site: New Dawn", "New Dawn"),
        ("New Dawn", "New Dawn"),
    ],
)
def test_tab_labels_drop_the_construction_site_prefix(overlay, station_name, expected):
    overlay.refresh(_state(_project(station_name=station_name)))

    assert _tab_texts(overlay)[0] == expected


def test_finished_and_failed_tabs_are_marked(overlay):
    overlay.refresh(_state(_project(1, complete=True), _project(2, failed=True)))

    assert _tab_texts(overlay)[1:3] == ["✔ New Dawn", "✖ New Dawn"]


def test_selecting_a_tab_remembers_it(overlay):
    overlay.refresh(_state(_project()))

    _select(overlay, COLONIZE_MARKET_ID)

    assert overlay.config.selected_market_id == COLONIZE_MARKET_ID


def test_tabs_step_round_in_both_directions(overlay):
    overlay.refresh(_state(_project()))
    _select(overlay, 1)

    overlay.select_next_tab()
    assert overlay._selected_market() == STATIONS_MARKET_ID
    overlay.select_next_tab()
    assert overlay._selected_market() == COLONIZE_MARKET_ID
    overlay.select_next_tab()
    assert overlay._selected_market() == 1  # wraps
    overlay.select_prev_tab()
    assert overlay._selected_market() == COLONIZE_MARKET_ID


#  page routing


def test_each_tab_shows_exactly_one_page(overlay):
    overlay.refresh(_state(_project()))

    for market_id, expected in (
        (COLONIZE_MARKET_ID, overlay.colonize_page),
        (STATIONS_MARKET_ID, overlay.stations_page),
        (1, overlay.commodity_page),
    ):
        _select(overlay, market_id)
        shown = [p for p in overlay._all_pages if not p.isHidden()]
        assert shown == [expected]


def test_a_project_tab_renders_that_project(overlay):
    overlay.refresh(_state(_project(1), _project(2, station_name="Second")))

    _select(overlay, 2)

    assert overlay.title_label.toolTip() == "Second"


def test_the_combined_tab_renders_every_construction_together(overlay):
    overlay.refresh(_state(_project(1), _project(2, station_name="Second")))

    _select(overlay, COMBINED_MARKET_ID)

    assert overlay.subtitle_label.toolTip() == "2 constructions"


def test_zero_projects_lands_on_the_colonize_tab(overlay):
    """It is the only tab, so it is what a commander without a colony sees."""
    overlay.refresh(_state())

    assert overlay._current_page is overlay.colonize_page
    assert overlay.title_label.toolTip() == "Colonisation targets"


def test_the_empty_view_stands_in_until_the_tab_bar_is_up(overlay):
    """With no tab to route by there is nothing to show but the placeholder."""
    overlay.hide()

    overlay.refresh(_state())

    assert overlay._current_page is overlay.commodity_page
    assert overlay.title_label.toolTip() == "No construction projects yet"
    assert overlay.project_icon_label.isHidden()


#  the header follows the page


def test_a_construction_header_shows_the_colonisation_emblem(overlay):
    overlay.refresh(_state(_project()))
    _select(overlay, 1)

    assert not overlay.project_icon_label.isHidden()
    assert overlay.title_label.toolTip() == "Construction Site: New Dawn"
    assert overlay.subtitle_label.toolTip() == "Sol"


def test_the_search_tabs_use_the_overlay_emblem(overlay):
    overlay.refresh(_state(_project()))
    expected = icons.app_glyph_pixmap(32).cacheKey()

    for market_id, title in (
        (STATIONS_MARKET_ID, "Nearest stations"),
        (COLONIZE_MARKET_ID, "Colonisation targets"),
    ):
        _select(overlay, market_id)
        assert not overlay.project_icon_label.isHidden()
        assert overlay.project_icon_label.pixmap().cacheKey() == expected
        assert overlay.title_label.toolTip() == title


def test_no_tab_carries_an_icon_of_its_own(overlay):
    """The emblem lives in the header; tabs stay text-only."""
    overlay.refresh(_state(_project()))

    for index in range(overlay.tabs.count()):
        assert overlay.tabs.tabIcon(index).isNull()


#  refreshing the current search


@pytest.mark.parametrize(
    ("market_id", "page_attr"),
    [
        (STATIONS_MARKET_ID, "stations_page"),
        (COLONIZE_MARKET_ID, "colonize_page"),
    ],
)
def test_refresh_targets_the_selected_search_tab_when_idle(
    overlay, market_id, page_attr
):
    overlay.refresh(_state(_project()))
    _select(overlay, market_id)
    page = getattr(overlay, page_attr)

    with mock.patch.object(page, "refresh") as refresh:
        overlay.refresh_current_search()
    refresh.assert_called_once_with()

    with (
        mock.patch.object(type(page), "busy", property(lambda _: True)),
        mock.patch.object(page, "refresh") as refresh,
    ):
        overlay.refresh_current_search()
    refresh.assert_not_called()


def test_refresh_ignores_a_commodity_tab(overlay):
    overlay.refresh(_state(_project()))
    _select(overlay, 1)

    with (
        mock.patch.object(overlay.stations_page, "refresh") as stations,
        mock.patch.object(overlay.colonize_page, "refresh") as colonize,
    ):
        overlay.refresh_current_search()

    stations.assert_not_called()
    colonize.assert_not_called()


#  project actions


def test_completing_a_construction_removes_it_and_its_tab(overlay):
    state = _state(_project(1), _project(2, station_name="Second"))
    overlay.refresh(state)
    _select(overlay, 1)
    removed = []
    overlay.project_removed.connect(lambda: removed.append(True))

    overlay.commodity_page.complete_requested.emit(1)

    assert 1 not in state.projects
    assert 1 not in _tab_data(overlay)
    assert removed == [True]


def test_completing_an_unknown_construction_is_inert(overlay):
    state = _state(_project(1))
    overlay.refresh(state)
    removed = []
    overlay.project_removed.connect(lambda: removed.append(True))

    overlay.commodity_page.complete_requested.emit(999)

    assert state.projects
    assert removed == []


def test_editing_carrier_cargo_asks_the_app_to_persist(overlay):
    overlay.refresh(_state(_project()))
    changed = []
    overlay.carrier_changed.connect(lambda: changed.append(True))

    overlay.commodity_page.carrier_edited.emit()

    assert changed == [True]


def test_a_page_asking_to_re_render_gets_one(overlay):
    state = _state(_project())
    overlay.refresh(state)

    with mock.patch.object(overlay, "refresh") as refresh:
        overlay.commodity_page.rerender_requested.emit()

    refresh.assert_called_once_with(state)


#  focus


def test_focus_detection_availability_is_published(overlay):
    assert overlay.focus_detection_available == overlay._detector.available
