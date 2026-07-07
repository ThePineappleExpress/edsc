import json

from edsc.journal.watcher import JournalWatcher


def _write(path, events):
    with path.open("a", encoding="utf-8") as fh:
        for e in events:
            fh.write(json.dumps(e) + "\n")


def test_tails_appended_lines(tmp_path):
    j = tmp_path / "Journal.2026-01-01T000000.01.log"
    _write(j, [{"event": "Fileheader"}])

    seen = []
    w = JournalWatcher(tmp_path, on_event=seen.append)
    w.prime_latest()  # seek to end; existing content not re-emitted

    _write(j, [{"event": "Docked", "MarketID": 1}, {"event": "Cargo"}])
    w.poll_once()

    assert [e["event"] for e in seen] == ["Docked", "Cargo"]


def test_handles_partial_then_completed_line(tmp_path):
    j = tmp_path / "Journal.2026-01-01T000000.01.log"
    j.write_text("", encoding="utf-8")
    seen = []
    w = JournalWatcher(tmp_path, on_event=seen.append)
    w.prime_latest()

    # Write half a line (no newline yet) -- must not emit.
    with j.open("a", encoding="utf-8") as fh:
        fh.write('{"event": "Docke')
    w.poll_once()
    assert seen == []

    # Complete the line.
    with j.open("a", encoding="utf-8") as fh:
        fh.write('d", "MarketID": 7}\n')
    w.poll_once()
    assert seen == [{"event": "Docked", "MarketID": 7}]


def test_rolls_over_to_newer_journal(tmp_path):
    old = tmp_path / "Journal.2026-01-01T000000.01.log"
    _write(old, [{"event": "Fileheader"}])
    seen = []
    w = JournalWatcher(tmp_path, on_event=seen.append)
    w.prime_latest()

    # Append to old, then a newer file appears with its own events.
    _write(old, [{"event": "Shutdown"}])
    new = tmp_path / "Journal.2026-01-02T000000.01.log"
    _write(new, [{"event": "LoadGame"}, {"event": "Docked", "MarketID": 9}])
    w.poll_once()

    events = [e["event"] for e in seen]
    # Tail of the old file is flushed before switching to the new one.
    assert events == ["Shutdown", "LoadGame", "Docked"]


def test_replay_history_reads_all_files(tmp_path):
    a = tmp_path / "Journal.2026-01-01T000000.01.log"
    b = tmp_path / "Journal.2026-01-02T000000.01.log"
    _write(a, [{"event": "A"}])
    _write(b, [{"event": "B"}])
    seen = []
    JournalWatcher(tmp_path, on_event=seen.append).replay_history()
    assert [e["event"] for e in seen] == ["A", "B"]


def test_cargo_reloaded_on_change(tmp_path):
    (tmp_path / "Journal.2026-01-01T000000.01.log").write_text("", encoding="utf-8")
    cargo_updates = []
    w = JournalWatcher(tmp_path, on_event=lambda e: None, on_cargo=cargo_updates.append)

    cargo = tmp_path / "Cargo.json"
    cargo.write_text(json.dumps({
        "event": "Cargo",
        "Inventory": [{"Name": "aluminium", "Count": 3}],
    }), encoding="utf-8")
    w.load_cargo_now()
    assert cargo_updates[-1] == [{"Name": "aluminium", "Count": 3}]


def test_replay_hands_tail_position_to_live_polling(tmp_path):
    """Events landing between replay and the first poll must not be lost, and
    replayed lines must not be re-emitted by the first poll."""
    j = tmp_path / "Journal.2026-01-01T000000.01.log"
    _write(j, [{"event": "A"}])
    seen = []
    w = JournalWatcher(tmp_path, on_event=seen.append)
    w.replay_history()

    w.poll_once()  # nothing new: must not re-emit A
    assert [e["event"] for e in seen] == ["A"]

    _write(j, [{"event": "B"}])
    w.poll_once()
    assert [e["event"] for e in seen] == ["A", "B"]


def test_srv_cargo_snapshot_is_ignored(tmp_path):
    (tmp_path / "Journal.2026-01-01T000000.01.log").write_text("", encoding="utf-8")
    cargo_updates = []
    w = JournalWatcher(tmp_path, on_event=lambda e: None,
                       on_cargo=cargo_updates.append)
    cargo = tmp_path / "Cargo.json"

    cargo.write_text(json.dumps({
        "event": "Cargo", "Vessel": "SRV", "Inventory": [],
    }), encoding="utf-8")
    w.load_cargo_now()
    assert cargo_updates == []  # SRV snapshot must not replace the ship hold

    cargo.write_text(json.dumps({
        "event": "Cargo", "Vessel": "Ship",
        "Inventory": [{"Name": "steel", "Count": 12}],
    }), encoding="utf-8")
    w.load_cargo_now()
    assert cargo_updates[-1] == [{"Name": "steel", "Count": 12}]
