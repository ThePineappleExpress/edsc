from edsc.flight_state import FlightStateTracker, ShipStatus


def _tracker(*events) -> FlightStateTracker:
    tracker = FlightStateTracker()
    for event in events:
        tracker.handle(event)
    return tracker


def test_unknown_state_fails_open():
    # Nothing heard yet: show the gizmos rather than hide them for no reason.
    assert FlightStateTracker().in_flight is True
    assert ShipStatus().in_flight is True


def test_docked_and_undocked_flip_flight():
    tracker = FlightStateTracker()
    assert tracker.handle({"event": "Docked"}) is True
    assert tracker.in_flight is False
    assert tracker.handle({"event": "Undocked"}) is True
    assert tracker.in_flight is True


def test_location_carries_the_state_at_session_start():
    # Location is the only event that says whether we start docked.
    assert _tracker({"event": "Location", "Docked": True}).in_flight is False
    assert _tracker({"event": "Location", "Docked": False}).in_flight is True


def test_location_without_a_docked_field_fails_open():
    assert _tracker({"event": "Location"}).in_flight is True


def test_load_game_marks_the_game_running_but_not_the_dock_state():
    # LoadGame carries no Docked field, so it must not imply one.
    tracker = _tracker({"event": "LoadGame"})
    assert tracker.status == ShipStatus(docked=None, running=True)
    assert tracker.in_flight is True


def test_shutdown_hides_the_gizmos():
    tracker = _tracker({"event": "Undocked"})
    assert tracker.in_flight is True
    assert tracker.handle({"event": "Shutdown"}) is True
    assert tracker.in_flight is False


def test_a_new_session_recovers_after_shutdown():
    tracker = _tracker(
        {"event": "Undocked"},
        {"event": "Shutdown"},
        {"event": "LoadGame"},
    )
    assert tracker.in_flight is True


def test_replaying_a_whole_session_lands_on_the_final_state():
    # Journals replay chronologically, so the newest event must win.
    tracker = _tracker(
        {"event": "Fileheader"},
        {"event": "LoadGame"},
        {"event": "Location", "Docked": True},
        {"event": "Undocked"},
        {"event": "SupercruiseEntry"},
        {"event": "StartJump"},
        {"event": "SupercruiseExit"},
        {"event": "Docked"},
    )
    assert tracker.in_flight is False
    assert tracker.status == ShipStatus(docked=True, running=True)


def test_a_crashed_session_without_shutdown_stays_open():
    # 3 of 35 real journals end mid-session; that must not strand us hidden.
    tracker = _tracker(
        {"event": "LoadGame"},
        {"event": "Location", "Docked": False},
        {"event": "NpcCrewPaidWage"},
    )
    assert tracker.in_flight is True


def test_irrelevant_events_report_no_change():
    tracker = _tracker({"event": "Undocked"})
    assert tracker.handle({"event": "Music"}) is False
    assert tracker.handle({"event": "Cargo"}) is False
    assert tracker.in_flight is True


def test_repeated_events_report_no_change():
    tracker = _tracker({"event": "Docked"})
    assert tracker.handle({"event": "Docked"}) is False


def test_malformed_events_are_ignored():
    tracker = FlightStateTracker()
    assert tracker.handle({}) is False
    assert tracker.handle({"event": None}) is False
    assert tracker.handle({"event": 42}) is False
    assert tracker.in_flight is True


def test_reset_forgets_everything():
    tracker = _tracker({"event": "Docked"})
    assert tracker.in_flight is False
    tracker.reset()
    assert tracker.status == ShipStatus()
    assert tracker.in_flight is True
