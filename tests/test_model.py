from edsc.model import COMBINED_MARKET_ID, AppState


DEPOT = {
    "timestamp": "2026-07-06T16:13:10Z",
    "event": "ColonisationConstructionDepot",
    "MarketID": 3952442114,
    "ConstructionProgress": 0.125,
    "ConstructionComplete": False,
    "ConstructionFailed": False,
    "ResourcesRequired": [
        {"Name": "$aluminium_name;", "Name_Localised": "Aluminium",
         "RequiredAmount": 842, "ProvidedAmount": 842, "Payment": 3239},
        {"Name": "$agriculturalmedicines_name;", "Name_Localised": "Agri-Medicines",
         "RequiredAmount": 115, "ProvidedAmount": 0, "Payment": 1655},
    ],
}

DOCKED = {
    "event": "Docked",
    "MarketID": 3952442114,
    "StationName": "Orbital Construction Site: Hartog Horizons",
    "StarSystem": "Pleiades Sector PD-S b4-1",
    "StationType": "SpaceConstructionDepot",
    "timestamp": "2026-07-06T16:00:00Z",
}


def test_depot_creates_project_with_lines():
    state = AppState()
    assert state.apply_event(DEPOT) is True
    proj = state.projects[3952442114]
    assert len(proj.lines) == 2
    assert proj.lines["aluminium"].required == 842
    assert proj.lines["aluminium"].provided == 842
    assert proj.lines["aluminium"].done is True
    assert proj.lines["agriculturalmedicines"].remaining == 115


def test_docked_names_the_project():
    state = AppState()
    state.apply_event(DOCKED)
    state.apply_event(DEPOT)
    proj = state.projects[3952442114]
    assert proj.station_name == "Orbital Construction Site: Hartog Horizons"
    assert proj.system_name == "Pleiades Sector PD-S b4-1"
    assert "Hartog Horizons" in proj.title


def test_rows_join_cargo_and_compute_short():
    state = AppState()
    state.apply_event(DEPOT)
    # Carrying 40 Agri-Medicines toward the 115 still required.
    state.set_cargo([
        {"Name": "agriculturalmedicines", "Name_Localised": "Agri-Medicines", "Count": 40},
    ])
    rows = {r.key: r for r in state.projects[3952442114].rows(state.cargo)}

    agri = rows["agriculturalmedicines"]
    assert agri.required == 115
    assert agri.provided == 0
    assert agri.in_cargo == 40
    assert agri.remaining == 115
    assert agri.short == 75  # 115 needed - 40 carried
    assert agri.can_complete_now is False

    alu = rows["aluminium"]
    assert alu.done is True
    assert alu.short == 0


def test_rows_sort_outstanding_before_done():
    state = AppState()
    state.apply_event(DEPOT)
    rows = state.projects[3952442114].rows(state.cargo)
    # Agri-Medicines (outstanding) must come before Aluminium (done).
    assert rows[0].key == "agriculturalmedicines"
    assert rows[-1].done is True


def test_can_complete_now_when_hold_covers_remaining():
    state = AppState()
    state.apply_event(DEPOT)
    state.set_cargo([{"Name": "agriculturalmedicines", "Count": 200}])
    row = {r.key: r for r in state.projects[3952442114].rows(state.cargo)}["agriculturalmedicines"]
    assert row.can_complete_now is True
    assert row.short == 0


def test_contribution_bumps_provided_optimistically():
    state = AppState()
    state.apply_event(DEPOT)
    state.apply_event({
        "event": "ColonisationContribution",
        "MarketID": 3952442114,
        "Contributions": [
            {"Name": "$Agriculturalmedicines_name;", "Name_Localised": "Agri-Medicines",
             "Amount": 50},
        ],
    })
    assert state.projects[3952442114].lines["agriculturalmedicines"].provided == 50


def test_depot_refresh_overwrites_provided_authoritatively():
    state = AppState()
    state.apply_event(DEPOT)
    # A later depot snapshot with different provided amounts replaces the lines.
    updated = dict(DEPOT)
    updated["ResourcesRequired"] = [
        {"Name": "$agriculturalmedicines_name;", "RequiredAmount": 115,
         "ProvidedAmount": 115, "Payment": 1655},
    ]
    state.apply_event(updated)
    proj = state.projects[3952442114]
    assert set(proj.lines) == {"agriculturalmedicines"}  # aluminium line dropped
    assert proj.lines["agriculturalmedicines"].done is True


def _depot(market_id, resources, **kw):
    return {
        "event": "ColonisationConstructionDepot",
        "MarketID": market_id,
        "ConstructionProgress": kw.get("progress", 0.0),
        "ConstructionComplete": kw.get("complete", False),
        "ConstructionFailed": kw.get("failed", False),
        "ResourcesRequired": [
            {"Name": f"${n}_name;", "Name_Localised": n.title(),
             "RequiredAmount": req, "ProvidedAmount": prov, "Payment": 1}
            for n, req, prov in resources
        ],
    }


def test_combined_project_sums_across_constructions():
    state = AppState()
    state.apply_event(_depot(1, [("steel", 100, 40), ("water", 50, 50)]))
    state.apply_event(_depot(2, [("steel", 200, 10), ("grain", 30, 0)]))

    combined = state.combined_project()
    assert combined.market_id == COMBINED_MARKET_ID
    # steel appears in both projects -> summed.
    assert combined.lines["steel"].required == 300
    assert combined.lines["steel"].provided == 50
    assert combined.lines["water"].required == 50
    assert combined.lines["grain"].required == 30
    assert combined.total_required() == 380
    assert combined.total_provided() == 100  # 40+50 (proj1) + 10+0 (proj2)


def test_combined_project_excludes_failed():
    state = AppState()
    state.apply_event(_depot(1, [("steel", 100, 0)]))
    state.apply_event(_depot(2, [("steel", 999, 0)], failed=True))
    combined = state.combined_project()
    assert combined.lines["steel"].required == 100  # failed project ignored
    assert [p.market_id for p in state.active_projects()] == [1]


def test_combined_rows_join_shared_cargo():
    state = AppState()
    state.apply_event(_depot(1, [("steel", 100, 0)]))
    state.apply_event(_depot(2, [("steel", 100, 0)]))
    state.set_cargo([{"Name": "steel", "Count": 30}])
    row = {r.key: r for r in state.combined_project().rows(state.cargo)}["steel"]
    assert row.required == 200
    assert row.in_cargo == 30
    assert row.short == 170


def test_cargo_transfer_tracks_carrier():
    state = AppState()
    state.apply_event({"event": "CargoTransfer", "Transfers": [
        {"Type": "steel", "Count": 500, "Direction": "tocarrier"},
        {"Type": "titanium", "Count": 200, "Direction": "tocarrier"},
    ]})
    assert state.carrier_cargo == {"steel": 500, "titanium": 200}
    # Move some back to the ship -> carrier decreases; SRV transfer ignored.
    state.apply_event({"event": "CargoTransfer", "Transfers": [
        {"Type": "steel", "Count": 500, "Direction": "toship"},   # -> removes steel
        {"Type": "titanium", "Count": 50, "Direction": "toship"},
        {"Type": "water", "Count": 10, "Direction": "tosrv"},      # ignored
    ]})
    assert state.carrier_cargo == {"titanium": 150}
    assert state.carrier_tracked_total() == 150


def test_carrier_stats_captures_identity_and_total():
    state = AppState()
    changed = state.apply_event({
        "event": "CarrierStats", "Name": "Grapplerman", "Callsign": "PQ7-designation",
        "SpaceUsage": {"Cargo": 10150},
    })
    assert changed is True
    assert state.carrier_callsign == "PQ7-designation"
    assert state.carrier_total == 10150


def test_rows_include_carrier_and_covered_by_stock():
    state = AppState()
    state.apply_event(_depot(1, [("steel", 100, 0)]))
    state.set_cargo([{"Name": "steel", "Count": 30}])
    state.apply_event({"event": "CargoTransfer", "Transfers": [
        {"Type": "steel", "Count": 80, "Direction": "tocarrier"}]})
    row = {r.key: r for r in state.projects[1].rows(state.cargo, state.carrier_cargo)}["steel"]
    assert row.in_cargo == 30
    assert row.on_carrier == 80
    assert row.short == 70  # still ship-based: 100 needed - 30 held
    assert row.covered_by_stock is True  # 30 hold + 80 carrier >= 100


def test_manual_carrier_amount_and_reset():
    state = AppState()
    state.set_carrier_amount("steel", 250)
    assert state.carrier_cargo["steel"] == 250
    state.set_carrier_amount("steel", 0)  # zero removes it
    assert "steel" not in state.carrier_cargo


def test_carrier_survives_restart_without_double_counting():
    """Reproduces the "pumped carrier on restart" bug.

    On startup the app loads the persisted state and then replays journal
    history on top. CargoTransfer deltas must not be re-applied to the loaded
    carrier snapshot, or the totals inflate every launch.
    """
    transfers = [
        {"event": "CargoTransfer", "timestamp": "2026-07-06T10:00:00Z",
         "Transfers": [{"Type": "steel", "Count": 500, "Direction": "tocarrier"}]},
        {"event": "CargoTransfer", "timestamp": "2026-07-06T11:00:00Z",
         "Transfers": [{"Type": "titanium", "Count": 200, "Direction": "tocarrier"}]},
    ]

    # First session: track transfers live.
    live = AppState()
    for ev in transfers:
        live.apply_event(ev)
    assert live.carrier_cargo == {"steel": 500, "titanium": 200}

    # Persist on exit, reload on next launch, then replay the SAME history.
    restarted = AppState.from_dict(live.to_dict())
    for ev in transfers:  # replay_history re-emits every past event
        restarted.apply_event(ev)

    # Numbers must match the live session, not be doubled.
    assert restarted.carrier_cargo == {"steel": 500, "titanium": 200}


def test_carrier_applies_transfers_newer_than_watermark():
    """Transfers that happened while the app was closed still get counted."""
    live = AppState()
    live.apply_event({
        "event": "CargoTransfer", "timestamp": "2026-07-06T10:00:00Z",
        "Transfers": [{"Type": "steel", "Count": 500, "Direction": "tocarrier"}],
    })
    restarted = AppState.from_dict(live.to_dict())
    # Replay the old (already-counted) transfer plus a new one from downtime.
    restarted.apply_event({
        "event": "CargoTransfer", "timestamp": "2026-07-06T10:00:00Z",
        "Transfers": [{"Type": "steel", "Count": 500, "Direction": "tocarrier"}],
    })
    restarted.apply_event({
        "event": "CargoTransfer", "timestamp": "2026-07-06T12:00:00Z",
        "Transfers": [{"Type": "steel", "Count": 100, "Direction": "tocarrier"}],
    })
    assert restarted.carrier_cargo == {"steel": 600}


def test_legacy_cache_without_watermark_is_not_reinflated():
    """A pre-watermark cache should trust its carrier snapshot on migration.

    Old state.json files have no ``last_event_time``; replaying history must not
    re-add transfers on top of the persisted amounts. The gate reopens after
    replay so live transfers still count.
    """
    old_transfer = {
        "event": "CargoTransfer", "timestamp": "2026-07-06T10:00:00Z",
        "Transfers": [{"Type": "steel", "Count": 500, "Direction": "tocarrier"}],
    }
    # Simulate a legacy cache dict: carrier amounts but no watermark field.
    legacy = {"carrier_cargo": {"steel": 500}}
    state = AppState.from_dict(legacy)

    # Replaying the historical transfer must NOT double the carrier amount.
    state.apply_event(old_transfer)
    assert state.carrier_cargo == {"steel": 500}

    # After replay the gate reopens: a genuinely new live transfer is counted.
    state.finish_replay()
    state.apply_event({
        "event": "CargoTransfer", "timestamp": "2026-07-06T13:00:00Z",
        "Transfers": [{"Type": "steel", "Count": 100, "Direction": "tocarrier"}],
    })
    assert state.carrier_cargo == {"steel": 600}


def test_serialisation_roundtrip():
    state = AppState()
    state.apply_event(DOCKED)
    state.apply_event(DEPOT)
    state.set_cargo([{"Name": "aluminium", "Count": 5}])
    state.apply_event({"event": "CarrierStats", "Callsign": "PQ7-XYZ",
                       "SpaceUsage": {"Cargo": 900}})
    state.apply_event({"event": "CargoTransfer", "Transfers": [
        {"Type": "aluminium", "Count": 300, "Direction": "tocarrier"}]})
    restored = AppState.from_dict(state.to_dict())
    assert restored.projects[3952442114].station_name == (
        "Orbital Construction Site: Hartog Horizons"
    )
    assert restored.projects[3952442114].lines["aluminium"].required == 842
    assert restored.cargo["aluminium"] == 5
    # Carrier tracking survives a save/load cycle.
    assert restored.carrier_cargo["aluminium"] == 300
    assert restored.carrier_callsign == "PQ7-XYZ"
    assert restored.carrier_total == 900


#  regression tests for the logic-hole fixes 


def test_outstanding_needs_subtracts_carrier_stock():
    """Stock staged on the carrier counts as acquired, per the docstring."""
    state = AppState()
    state.apply_event(_depot(1, [("steel", 100, 0), ("water", 60, 0)]))
    state.set_cargo([{"Name": "steel", "Count": 30}])
    state.apply_event({"event": "CargoTransfer", "Transfers": [
        {"Type": "steel", "Count": 70, "Direction": "tocarrier"},
        {"Type": "water", "Count": 10, "Direction": "tocarrier"},
    ]})
    needs = state.outstanding_needs()
    assert "Steel" not in needs  # 30 in hold + 70 on carrier covers 100
    assert needs["Water"] == 50  # 60 required - 10 on carrier


def test_srv_cargo_snapshot_does_not_wipe_ship_hold():
    state = AppState()
    state.apply_event({"event": "Cargo", "Vessel": "Ship",
                       "Inventory": [{"Name": "steel", "Count": 64}]})
    assert state.cargo == {"steel": 64}
    changed = state.apply_event({"event": "Cargo", "Vessel": "SRV",
                                 "Inventory": []})
    assert changed is False
    assert state.cargo == {"steel": 64}


def test_empty_inline_cargo_inventory_clears_hold():
    state = AppState()
    state.apply_event({"event": "Cargo",
                       "Inventory": [{"Name": "steel", "Count": 64}]})
    assert state.apply_event({"event": "Cargo", "Inventory": []}) is True
    assert state.cargo == {}


def test_live_transfer_in_same_second_as_replay_end_is_applied():
    """The replay gate must not swallow a live transfer that lands within the
    same second the replayed history ended (journal ts resolution is 1 s)."""
    live = AppState()
    old = {"event": "CargoTransfer", "timestamp": "2026-07-06T10:00:00Z",
           "Transfers": [{"Type": "steel", "Count": 100, "Direction": "tocarrier"}]}
    live.apply_event(old)
    restarted = AppState.from_dict(live.to_dict())
    restarted.apply_event(old)  # replayed duplicate: skipped by the gate
    restarted.finish_replay()
    restarted.apply_event({  # genuinely new, but in the very same second
        "event": "CargoTransfer", "timestamp": "2026-07-06T10:00:00Z",
        "Transfers": [{"Type": "steel", "Count": 50, "Direction": "tocarrier"}],
    })
    assert restarted.carrier_cargo == {"steel": 150}


def test_docked_reports_change_for_new_market():
    state = AppState()
    assert state.apply_event(DOCKED) is True
    assert state.apply_event(DOCKED) is False  # same dock again: no change


def test_from_dict_skips_malformed_projects():
    data = {"projects": [
        {"station_name": "broken, no market id"},
        {"market_id": "not-a-number"},
        {"market_id": 5, "lines": [{"key": "steel", "required": 10}]},
    ]}
    state = AppState.from_dict(data)
    assert list(state.projects) == [5]
    assert state.projects[5].lines["steel"].required == 10


def test_display_names_survive_cache_round_trip():
    from edsc import commodities

    state = AppState()
    state.apply_event({
        "event": "ColonisationConstructionDepot", "MarketID": 9,
        "ResourcesRequired": [
            {"Name": "$edsc_testium_name;", "Name_Localised": "EDSC Testium",
             "RequiredAmount": 10, "ProvidedAmount": 0},
        ],
    })
    payload = state.to_dict()
    # Simulate a fresh process whose registry never saw the journal event.
    commodities._DISPLAY_REGISTRY.pop("edsc_testium", None)
    restored = AppState.from_dict(payload)
    row = restored.projects[9].rows({})[0]
    assert row.name == "EDSC Testium"  # not the "Edsc Testium" fallback


def test_removed_project_stays_removed_until_new_depot_event():
    state = AppState()
    depot_old = _depot(7, [("steel", 10, 0)])
    depot_old["timestamp"] = "2026-07-06T10:00:00Z"
    state.apply_event(depot_old)
    assert state.remove_project(7) is True
    assert 7 not in state.projects

    # Replaying the same historical depot event must not resurrect it,
    # including after a save/load cycle.
    state.apply_event(depot_old)
    assert 7 not in state.projects
    reloaded = AppState.from_dict(state.to_dict())
    reloaded.apply_event(depot_old)
    assert 7 not in reloaded.projects

    # Docking there again (a newer depot snapshot) brings it back.
    depot_new = dict(depot_old)
    depot_new["timestamp"] = "2026-07-06T12:00:00Z"
    reloaded.apply_event(depot_new)
    assert 7 in reloaded.projects
