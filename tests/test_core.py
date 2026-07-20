import json
import os
from datetime import datetime, timezone

from edsc import core
from edsc.model import AppState


def _epoch(iso: str) -> float:
    return (
        datetime.strptime(iso, "%Y-%m-%dT%H:%M:%SZ")
        .replace(tzinfo=timezone.utc)
        .timestamp()
    )


def _journal(tmp_path, name: str, mtime_iso: str):
    p = tmp_path / name
    p.write_text("", encoding="utf-8")
    t = _epoch(mtime_iso)
    os.utime(p, (t, t))
    return p


def test_journals_to_replay_skips_files_older_than_watermark(tmp_path):
    old = _journal(tmp_path, "Journal.2026-01-01T000000.01.log", "2026-01-01T02:00:00Z")
    new = _journal(tmp_path, "Journal.2026-07-06T000000.01.log", "2026-07-06T02:00:00Z")
    files = core.journals_to_replay(tmp_path, "2026-07-05T00:00:00Z")
    assert files == [new]
    assert old not in files


def test_journals_to_replay_keeps_everything_without_watermark(tmp_path):
    a = _journal(tmp_path, "Journal.2026-01-01T000000.01.log", "2026-01-01T02:00:00Z")
    b = _journal(tmp_path, "Journal.2026-07-06T000000.01.log", "2026-07-06T02:00:00Z")
    assert core.journals_to_replay(tmp_path, "") == [a, b]
    # A malformed watermark is treated the same as none at all.
    assert core.journals_to_replay(tmp_path, "garbage") == [a, b]


def test_journals_to_replay_keeps_files_near_the_watermark(tmp_path):
    """The mtime comparison has a safety margin: files written shortly before the watermark are still replayed rather than risking lost events."""
    near = _journal(tmp_path, "Journal.2026-07-04T000000.01.log", "2026-07-04T23:45:00Z")
    files = core.journals_to_replay(tmp_path, "2026-07-05T00:00:00Z")
    assert files == [near]


#  manual-sync readers


def test_read_session_events_parses_only_newest_journal(tmp_path):
    (tmp_path / "Journal.2026-01-01T000000.01.log").write_text(
        '{"event":"Fileheader"}\n', encoding="utf-8"
    )
    (tmp_path / "Journal.2026-07-06T000000.01.log").write_text(
        '{"event":"LoadGame","Horizons":true}\n'
        "not valid json\n"
        "\n"
        '{"event":"Docked","MarketID":42}\n',
        encoding="utf-8",
    )
    events = core.read_session_events(tmp_path)
    # Only the newest file, malformed/blank lines skipped, order preserved.
    assert [e["event"] for e in events] == ["LoadGame", "Docked"]


def test_read_session_events_empty_without_journals(tmp_path):
    assert core.read_session_events(tmp_path) == []


def test_read_market_snapshot_reads_dict(tmp_path):
    (tmp_path / "Market.json").write_text(
        '{"MarketID":42,"Items":[]}', encoding="utf-8"
    )
    assert core.read_market_snapshot(tmp_path) == {"MarketID": 42, "Items": []}


def test_read_market_snapshot_missing_or_malformed(tmp_path):
    assert core.read_market_snapshot(tmp_path) is None
    (tmp_path / "Market.json").write_text("not json", encoding="utf-8")
    assert core.read_market_snapshot(tmp_path) is None


#  state persistence


def _depot_event(market_id, ts):
    return {
        "event": "ColonisationConstructionDepot",
        "timestamp": ts,
        "MarketID": market_id,
        "ConstructionProgress": 0.1,
        "ConstructionComplete": False,
        "ConstructionFailed": False,
        "ResourcesRequired": [
            {"Name": "$steel_name;", "Name_Localised": "Steel",
             "RequiredAmount": 100, "ProvidedAmount": 10, "Payment": 1},
        ],
    }


def test_state_survives_a_save_load_round_trip(tmp_path, monkeypatch):
    monkeypatch.setattr(core.paths, "state_dir", lambda: tmp_path)
    state = AppState()
    state.apply_event(_depot_event(7, "2026-07-06T12:00:00Z"))

    core.save_state(state)
    loaded = core.load_cached_state()

    assert loaded.projects[7].lines["steel"].required == 100
    assert loaded.last_event_time == "2026-07-06T12:00:00Z"


def test_load_cached_state_tolerates_missing_and_corrupt_files(tmp_path, monkeypatch):
    monkeypatch.setattr(core.paths, "state_dir", lambda: tmp_path)
    assert core.load_cached_state().projects == {}
    core.state_file().write_text("not json", encoding="utf-8")
    assert core.load_cached_state().projects == {}


#  bootstrap


def _write_journal(tmp_path, name, events, mtime_iso):
    p = tmp_path / name
    p.write_text(
        "".join(json.dumps(e) + "\n" for e in events), encoding="utf-8"
    )
    t = _epoch(mtime_iso)
    os.utime(p, (t, t))
    return p


TRANSFER = {
    "event": "CargoTransfer", "timestamp": "2026-07-06T12:30:00Z",
    "Transfers": [{"Type": "steel", "Count": 500, "Direction": "tocarrier"}],
}


def _session_dir(tmp_path):
    _write_journal(
        tmp_path, "Journal.2026-07-06T000000.01.log",
        [_depot_event(7, "2026-07-06T12:00:00Z"), TRANSFER],
        "2026-07-06T13:00:00Z",
    )
    (tmp_path / "Cargo.json").write_text(json.dumps({
        "event": "Cargo", "Inventory": [{"Name": "steel", "Count": 30}],
    }), encoding="utf-8")
    return tmp_path


def test_bootstrap_reconstructs_state_and_hands_over_to_live_polling(tmp_path):
    _session_dir(tmp_path)

    state, watcher = core.bootstrap(tmp_path)

    assert state.projects[7].lines["steel"].required == 100
    assert state.carrier_cargo == {"steel": 500}
    assert state.cargo == {"steel": 30}

    # The first poll sees nothing new and must not re-apply replayed history.
    watcher.poll_once()
    assert state.carrier_cargo == {"steel": 500}

    # A genuinely new line lands: bootstrap reopened the replay gate.
    live = {"event": "CargoTransfer", "timestamp": "2026-07-06T14:00:00Z",
            "Transfers": [{"Type": "steel", "Count": 100,
                           "Direction": "tocarrier"}]}
    with (tmp_path / "Journal.2026-07-06T000000.01.log").open(
        "a", encoding="utf-8"
    ) as fh:
        fh.write(json.dumps(live) + "\n")
    watcher.poll_once()
    assert state.carrier_cargo == {"steel": 600}


def test_restart_replays_on_top_of_cache_without_double_counting(
    tmp_path, monkeypatch
):
    """The pumped-carrier regression, end to end through core: the journal's mtime is inside the watermark margin, so the restart replays the same file on top of the loaded cache -- the replay gate must swallow the duplicates."""
    monkeypatch.setattr(core.paths, "state_dir", lambda: tmp_path / "state")
    _session_dir(tmp_path)

    first, _ = core.bootstrap(tmp_path)
    core.save_state(first)

    second, _ = core.bootstrap(tmp_path, core.load_cached_state())
    assert second.carrier_cargo == {"steel": 500}  # not 1000
    assert second.projects[7].lines["steel"].required == 100


def test_bootstrap_with_an_empty_journal_dir_starts_clean(tmp_path):
    state, watcher = core.bootstrap(tmp_path)
    assert state.projects == {}
    watcher.poll_once()  # no journals yet: polling is harmless
