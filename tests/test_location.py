from edsc.model import AppState

FSD_JUMP = {
    "event": "FSDJump",
    "StarSystem": "Pleiades Sector PD-S b4-1",
    "StarPos": [-81.62, -151.31, -383.53],
    "timestamp": "2026-07-06T16:20:00Z",
}

LOCATION = {
    "event": "Location",
    "StarSystem": "Sol",
    "StarPos": [0.0, 0.0, 0.0],
    "timestamp": "2026-07-06T15:00:00Z",
}

DOCKED = {
    "event": "Docked",
    "MarketID": 3952442114,
    "StationName": "Jameson Memorial",
    "StarSystem": "Shinrarta Dezhra",
    "timestamp": "2026-07-06T16:00:00Z",
}


def test_fsdjump_tracks_system_and_coords():
    state = AppState()
    assert state.apply_event(FSD_JUMP) is True
    assert state.current_system == "Pleiades Sector PD-S b4-1"
    assert state.current_coords == (-81.62, -151.31, -383.53)


def test_location_event_tracks_system():
    state = AppState()
    assert state.apply_event(LOCATION) is True
    assert state.current_system == "Sol"
    assert state.current_coords == (0.0, 0.0, 0.0)


def test_docked_updates_current_system():
    state = AppState()
    state.apply_event(DOCKED)
    assert state.current_system == "Shinrarta Dezhra"


def test_location_persists_round_trip():
    state = AppState()
    state.apply_event(FSD_JUMP)
    restored = AppState.from_dict(state.to_dict())
    assert restored.current_system == "Pleiades Sector PD-S b4-1"
    assert restored.current_coords == (-81.62, -151.31, -383.53)


def test_repeated_location_reports_no_change():
    state = AppState()
    assert state.apply_event(FSD_JUMP) is True
    assert state.apply_event(FSD_JUMP) is False


def test_outstanding_needs_reflects_shortfall():
    state = AppState()
    depot = {
        "event": "ColonisationConstructionDepot",
        "MarketID": 42,
        "ResourcesRequired": [
            {"Name": "$aluminium_name;", "Name_Localised": "Aluminium",
             "RequiredAmount": 100, "ProvidedAmount": 0},
            {"Name": "$steel_name;", "Name_Localised": "Steel",
             "RequiredAmount": 50, "ProvidedAmount": 50},
        ],
    }
    state.apply_event(depot)
    # Steel is fully provided -> not needed; Aluminium is short 100.
    needs = state.outstanding_needs()
    assert needs == {"Aluminium": 100}
