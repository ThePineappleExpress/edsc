import pytest

from edsc import boost
from edsc.boost import BoostState, BoostTracker

#  Loadout parsing + ship lookup


def test_read_loadout_extracts_ship():
    assert boost.read_loadout({"event": "Loadout", "Ship": "panthermkii"}) == (
        "panthermkii"
    )


def test_read_loadout_tolerates_missing_or_wrong_types():
    assert boost.read_loadout({"event": "Loadout"}) is None
    assert boost.read_loadout({"Ship": 42}) is None
    assert boost.read_loadout({}) is None


def test_boost_interval_lookup_is_case_insensitive():
    # EDSY's boostint: the Panther Clipper is 6.5s.
    assert boost.boost_interval_for("PantherMkII") == 6.5
    assert boost.boost_interval_for("panthermkii") == 6.5
    assert boost.boost_interval_for("sidewinder") == 4.0
    assert boost.boost_interval_for("typex") == 6.0  # Alliance Chieftain


def test_unknown_ship_has_no_interval():
    assert boost.boost_interval_for("no_such_ship") is None
    assert boost.boost_interval_for("") is None
    assert boost.boost_interval_for(None) is None


#  the live tracker


def _panther() -> BoostTracker:
    tracker = BoostTracker()
    tracker.set_ship("panthermkii")  # 6.5s interval
    return tracker


def test_starts_ready():
    tracker = _panther()
    assert tracker.available is True
    assert tracker.interval == 6.5
    assert tracker.state is BoostState.READY
    assert tracker.remaining == 0.0
    assert tracker.fraction == 1.0


def test_boost_then_countdown_cycle():
    tracker = _panther()
    assert tracker.boost() is True
    assert tracker.state is BoostState.COOLING
    assert tracker.remaining == pytest.approx(6.5)

    tracker.advance(3.0)
    assert tracker.state is BoostState.COOLING
    assert tracker.remaining == pytest.approx(3.5)

    # Into the final second, imminent.
    tracker.advance(3.0)
    assert tracker.state is BoostState.IMMINENT
    assert tracker.remaining <= boost.IMMINENT_SECONDS

    # Past the interval, ready again (clamped at zero, never negative).
    tracker.advance(2.0)
    assert tracker.state is BoostState.READY
    assert tracker.remaining == 0.0


def test_fraction_tracks_the_countdown():
    tracker = _panther()
    tracker.boost()
    assert tracker.fraction == pytest.approx(0.0)
    tracker.advance(3.25)  # half of 6.5
    assert tracker.fraction == pytest.approx(0.5)


def test_a_press_while_cooling_is_ignored():
    tracker = _panther()
    tracker.boost()
    tracker.advance(2.0)
    remaining = tracker.remaining
    assert tracker.boost() is False  # does nothing in-game either
    assert tracker.remaining == remaining


def test_unknown_ship_is_unavailable():
    tracker = BoostTracker()
    tracker.set_ship("no_such_ship")
    assert tracker.available is False
    assert tracker.state is BoostState.UNAVAILABLE
    assert tracker.boost() is False
    assert tracker.fraction == 1.0


def test_ship_swap_resets_to_ready_with_the_new_interval():
    tracker = _panther()
    tracker.boost()
    assert tracker.state is BoostState.COOLING
    # Swapping hulls means a fresh countdown and the new ship's interval.
    tracker.set_ship("sidewinder")
    assert tracker.state is BoostState.READY
    assert tracker.interval == 4.0


def test_setting_the_same_ship_does_not_reset_the_countdown():
    tracker = _panther()
    tracker.boost()
    tracker.advance(2.0)
    remaining = tracker.remaining
    tracker.set_ship("panthermkii")  # same hull
    assert tracker.remaining == remaining


def test_advance_is_inert_when_ready_or_unavailable():
    ready = _panther()
    ready.advance(5.0)
    assert ready.remaining == 0.0

    unknown = BoostTracker()
    unknown.set_ship(None)
    unknown.advance(5.0)  # must not raise
    assert unknown.state is BoostState.UNAVAILABLE
