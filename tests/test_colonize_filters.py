"""Tests for the Colonize tab's filter deck widget."""

# SPDX-License-Identifier: GPL-3.0-or-later

import pytest

from edsc.config import Config
from edsc.gui.colonize_filters import ColonizeFilters
from edsc.systems import SystemFilters


@pytest.fixture
def panel(qapp):
    config = Config()
    return ColonizeFilters(config), config


def test_sync_from_config_loads_every_control(panel):
    widget, config = panel
    config.colonize_range_ly = 75
    config.colonize_min_bodies = 6
    config.colonize_max_hops = 3
    config.colonize_min_stars = 2
    config.colonize_terraformable_only = True
    config.colonize_body_types = ["ELW", "WW"]
    config.colonize_star_types = ["NS"]
    config.colonize_ring_types = ["Metallic"]
    config.colonize_sort = "nearest"
    widget.sync_from_config()

    assert widget.range_ly() == 75
    assert widget.sort() == "nearest"
    f = widget.system_filters()
    assert f == SystemFilters(
        min_bodies=6,
        max_hops=3,
        min_stars=2,
        terraformable_only=True,
        body_types=("ELW", "WW"),
        star_types=("NS",),
        ring_types=("Metallic",),
    )


def test_live_toggle_persists_and_emits_changed(panel):
    widget, config = panel
    fired = []
    widget.changed.connect(lambda: fired.append(widget.system_filters()))

    widget._toggles["HMC"].setChecked(True)

    assert fired and "HMC" in fired[-1].body_types
    assert "HMC" in config.colonize_body_types


def test_radius_edits_defer_to_search_not_filter(panel):
    widget, config = panel
    changed, edited, searched = [], [], []
    widget.changed.connect(lambda: changed.append(True))
    widget.rangeEdited.connect(lambda: edited.append(widget.range_ly()))
    widget.searchRequested.connect(lambda: searched.append(True))

    widget._range.setValue(150)
    assert edited == [150]
    assert changed == []  # radius must not trigger a live re-filter
    assert config.colonize_range_ly == 150

    widget._refresh_btn.click()
    assert searched == [True]


def test_every_toggle_is_reachable_and_persists(panel):
    # No collapsible bands any more: every toggle is always laid out and live.
    widget, config = panel
    for key in ("terra", "ELW", "scoop", "NS"):
        assert key in widget._toggles
    widget._toggles["scoop"].setChecked(True)
    widget._toggles["NS"].setChecked(True)
    assert set(config.colonize_star_types) == {"scoop", "NS"}


def test_set_range_ly_updates_without_emitting(panel):
    widget, _config = panel
    changed = []
    widget.changed.connect(lambda: changed.append(True))
    widget.rangeEdited.connect(lambda: changed.append(True))
    widget.set_range_ly(300)
    assert widget.range_ly() == 300
    assert changed == []  # programmatic sync is silent
