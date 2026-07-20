"""Tests for the commodity list page."""

# SPDX-License-Identifier: GPL-3.0-or-later

from unittest import mock

import pytest

from edsc.config import Config
from edsc.gui.commodity_page import CommodityPage
from edsc.model import COMBINED_MARKET_ID, AppState, CommodityLine, Project


@pytest.fixture
def page(qapp):
    widget = CommodityPage(Config(hide_completed=False))
    yield widget
    widget.deleteLater()
    qapp.processEvents()


def _project(market_id=1, delivered=0, required=100, **overrides):
    values = {
        "market_id": market_id,
        "station_name": "Construction Site: New Dawn",
        "system_name": "Sol",
        "lines": {
            "steel": CommodityLine(key="steel", required=required, provided=delivered)
        },
    }
    values.update(overrides)
    return Project(**values)


def _state(**attrs):
    state = AppState()
    for name, value in attrs.items():
        setattr(state, name, value)
    return state


#  rendering


def test_rendering_a_project_fills_the_header_progress_and_totals(page):
    page.render(_project(delivered=25, required=100), _state())

    assert page.title == "Construction Site: New Dawn"
    assert page.subtitle == "Sol"
    assert page.progress.value() == 25
    assert page.percent_label.text() == "25%"
    assert page.totals_label.toolTip() == "25 / 100 t delivered · 75 t to go"
    assert page.model.rowCount() == 1


def test_a_finished_project_is_flagged_in_the_subtitle(page):
    page.render(_project(complete=True), _state())
    assert page.subtitle == "Sol · COMPLETE"

    page.render(_project(complete=False, failed=True), _state())
    assert page.subtitle == "Sol · FAILED"


def test_the_combined_view_takes_the_subtitle_it_is_given(page):
    page.render(_project(), _state(), subtitle="3 constructions")

    assert page.subtitle == "3 constructions"


def test_the_empty_view_says_what_to_do_and_drops_the_emblem(page):
    page.render(_project(), _state())
    assert page.header_icon() is not None

    page.render_empty()

    assert page.title == "No construction projects yet"
    assert page.subtitle == "Dock at a colonisation construction site"
    assert page.header_icon() is None  # nothing to illustrate
    assert page.progress.value() == 0
    assert page.model.rowCount() == 0
    assert page.totals_label.toolTip() == ""


def test_a_project_view_shows_the_colonisation_emblem(page):
    page.render(_project(), _state())

    assert page.header_icon() is not None


#  completing a construction


def test_a_delivered_project_offers_to_be_cleared(page):
    page.render(_project(delivered=100, required=100), _state())

    assert not page.complete_btn.isHidden()


def test_an_unfinished_project_offers_no_clear_button(page):
    page.render(_project(delivered=25, required=100), _state())

    assert page.complete_btn.isHidden()


def test_the_combined_view_can_never_be_completed_as_one(page):
    """It aggregates several sites, so there is no single thing to clear."""
    page.render(
        _project(market_id=COMBINED_MARKET_ID, delivered=100, required=100), _state()
    )

    assert page.complete_btn.isHidden()


def test_clearing_a_construction_reports_which_one(page):
    page.render(_project(market_id=77, delivered=100, required=100), _state())
    removed = []
    page.complete_requested.connect(removed.append)

    page.complete_btn.click()

    assert removed == [77]


def test_clearing_is_inert_on_the_empty_view(page):
    page.render_empty()
    removed = []
    page.complete_requested.connect(removed.append)

    page._complete_construction()

    assert removed == []


#  fleet carrier


def test_no_carrier_means_no_carrier_line(page):
    page.render(_project(), _state())

    assert page.carrier_label.isHidden()


def test_a_tracked_carrier_reports_its_tonnage(page):
    state = _state(carrier_callsign="K1T-00A", carrier_cargo={"steel": 40})
    page.render(_project(), state)

    assert not page.carrier_label.isHidden()
    assert page.carrier_label.text() == "FC K1T-00A · tracking 40 t"


def test_tracking_more_than_the_carrier_holds_is_flagged_as_an_error(page):
    """Phantom coverage: e.g. stock sold via the carrier market."""
    state = _state(
        carrier_callsign="K1T-00A", carrier_cargo={"steel": 90}, carrier_total=50
    )
    page.render(_project(), state)

    text = page.carrier_label.text()
    assert "exceeds the 50 t aboard" in text
    assert "'FC…' to correct" in text


def test_untracked_tonnage_aboard_is_reported_as_informational(page):
    state = _state(
        carrier_callsign="K1T-00A", carrier_cargo={"steel": 40}, carrier_total=100
    )
    page.render(_project(), state)

    assert "60 t aboard untracked" in page.carrier_label.text()


def test_an_unnamed_carrier_still_reports_its_cargo(page):
    page.render(_project(), _state(carrier_cargo={"steel": 40}))

    assert page.carrier_label.text() == "Fleet carrier · tracking 40 t"


def test_editing_carrier_cargo_writes_it_back_and_announces_the_change(page):
    state = _state(carrier_callsign="K1T-00A", carrier_cargo={"steel": 10})
    page.render(_project(), state)
    edited = []
    page.carrier_edited.connect(lambda: edited.append(True))

    with mock.patch(
        "edsc.gui.commodity_page.CarrierCargoDialog"
    ) as dialog_cls:
        dialog_cls.return_value.exec.return_value = True
        dialog_cls.return_value.values.return_value = {"steel": 55}
        page._edit_carrier_cargo()

    assert state.carrier_cargo["steel"] == 55
    assert edited == [True]


def test_a_cancelled_carrier_edit_changes_nothing(page):
    state = _state(carrier_callsign="K1T-00A", carrier_cargo={"steel": 10})
    page.render(_project(), state)
    edited = []
    page.carrier_edited.connect(lambda: edited.append(True))

    with mock.patch(
        "edsc.gui.commodity_page.CarrierCargoDialog"
    ) as dialog_cls:
        dialog_cls.return_value.exec.return_value = False
        page._edit_carrier_cargo()

    assert state.carrier_cargo == {"steel": 10}
    assert edited == []


#  toggles


def test_hiding_done_items_persists_and_asks_for_a_re_render(page):
    """The model filters in set_rows, so the toggle alone can't take effect."""
    page.render(_project(), _state())
    rerenders = []
    page.rerender_requested.connect(lambda: rerenders.append(True))

    page.hide_done_btn.setChecked(True)

    assert page.config.hide_completed is True
    assert rerenders == [True]


def test_hidden_done_items_drop_out_on_the_next_render(page):
    page.hide_done_btn.setChecked(True)

    page.render(_project(delivered=100, required=100), _state())

    assert page.model.rowCount() == 0  # the only line is delivered


def test_settings_changes_are_mirrored_without_asking_for_a_re_render(page):
    page.config.hide_completed = True
    rerenders = []
    page.rerender_requested.connect(lambda: rerenders.append(True))

    page.sync_from_config()

    assert page.hide_done_btn.isChecked()
    assert rerenders == []  # the shell re-renders after sync anyway
