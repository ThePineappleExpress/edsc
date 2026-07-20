"""Tests for the engine's worker-signal guards and shutdown; the worker signals are emitted through real connections (never by calling the slots directly) because the guards key off ``sender()``, which only a delivered signal sets."""

# SPDX-License-Identifier: GPL-3.0-or-later

from pathlib import Path
from types import SimpleNamespace

import pytest

from edsc import engine as engine_module
from edsc.config import Config
from edsc.engine import Engine, _Worker
from edsc.model import AppState

DOCKED = {
    "event": "Docked",
    "MarketID": 42,
    "StationName": "Hartog Horizons",
    "StarSystem": "Sol",
    "timestamp": "2026-07-06T16:00:00Z",
}


class _StubUplink:
    """Records what the engine feeds the EDDN relay."""

    def __init__(self):
        self.armed = 0
        self.events = []
        self.markets = []

    def arm(self):
        self.armed += 1

    def handle_event(self, event):
        self.events.append(event)

    def handle_market(self, data):
        self.markets.append(data)

    def sync_now(self, events, market):
        self.synced = (events, market)
        return (True, False)


@pytest.fixture
def eng(qapp):
    e = Engine(Config())
    yield e
    e.deleteLater()
    qapp.processEvents()


def _worker(eng):
    """A real worker wired to the engine exactly as ``start()`` wires its own, but never run -- the test emits its signals by hand."""
    worker = _Worker(Path("/nonexistent"))
    worker.ready.connect(eng._on_ready)
    worker.failed.connect(eng._on_failed)
    worker.event.connect(eng._on_event)
    worker.cargo.connect(eng._on_cargo)
    worker.market.connect(eng._on_market)
    return worker


def _spy(signal):
    calls = []
    signal.connect(lambda *args: calls.append(args))
    return calls


#  the current worker drives the engine


def test_current_worker_signals_update_state_and_feed_the_uplink(eng):
    worker = _worker(eng)
    eng._worker = worker
    eng.uplink = _StubUplink()
    changed = _spy(eng.state_changed)
    live = _spy(eng.live_event)

    worker.event.emit(DOCKED)
    assert eng.state.docked_market_id == 42
    assert eng.uplink.events == [DOCKED]
    assert live == [(DOCKED,)]
    assert len(changed) == 1

    worker.cargo.emit([{"Name": "steel", "Count": 3}])
    assert eng.state.cargo == {"steel": 3}

    market = {"event": "Market", "MarketID": 42, "Items": []}
    worker.market.emit(market)
    assert eng.uplink.markets == [market]
    assert len(changed) == 3


def test_ready_hands_over_state_and_arms_the_uplink(eng):
    worker = _worker(eng)
    eng._worker = worker
    eng.uplink = _StubUplink()
    statuses = _spy(eng.status_changed)
    ready = _spy(eng.ready)

    replayed, watcher = AppState(), object()
    worker.ready.emit(replayed, watcher)

    assert eng.state is replayed
    assert eng.watcher is watcher
    assert eng.uplink.armed == 1
    assert ready == [()]
    assert "Watching" in statuses[-1][0]


def test_failed_reports_the_replay_error(eng):
    worker = _worker(eng)
    eng._worker = worker
    statuses = _spy(eng.status_changed)

    worker.failed.emit("boom")

    assert statuses == [("Journal replay failed: boom",)]


#  a superseded worker's queued signals must not touch the new state


def test_superseded_worker_signals_are_all_ignored(eng):
    old = _worker(eng)
    eng._worker = object()  # a restart replaced the worker
    eng.uplink = _StubUplink()
    changed = _spy(eng.state_changed)
    live = _spy(eng.live_event)
    statuses = _spy(eng.status_changed)

    old.event.emit(DOCKED)
    old.cargo.emit([{"Name": "steel", "Count": 3}])
    old.market.emit({"event": "Market", "MarketID": 42, "Items": []})
    old.failed.emit("boom")

    assert eng.state.docked_market_id is None
    assert eng.state.cargo == {}
    assert eng.uplink.events == [] and eng.uplink.markets == []
    assert changed == [] and live == [] and statuses == []


def test_superseded_ready_does_not_swap_state_or_arm(eng):
    old = _worker(eng)
    original_state = eng.state
    eng._worker = object()
    eng.uplink = _StubUplink()

    old.ready.emit(AppState(), object())

    assert eng.state is original_state
    assert eng.watcher is None
    assert eng.uplink.armed == 0


#  stop()


@pytest.fixture
def saved(monkeypatch):
    """Capture persisted states; stop() must never write the real state dir."""
    states = []
    monkeypatch.setattr(engine_module.core, "save_state", states.append)
    return states


def test_stop_joins_the_thread_and_persists_state(eng, saved):
    worker = _worker(eng)
    thread = SimpleNamespace(quits=0, quit=None, wait=lambda ms: True)
    thread.quit = lambda: setattr(thread, "quits", thread.quits + 1)
    eng._worker, eng._thread = worker, thread

    eng.stop()

    assert worker._stop.is_set()
    assert thread.quits == 1
    assert eng._orphans == []
    assert eng._worker is None and eng._thread is None
    assert saved == [eng.state]


def test_stop_keeps_an_unfinished_worker_as_an_ignored_orphan(eng, saved):
    """A worker stuck replaying a huge file outlives the grace period: it must be kept referenced (not garbage-collected mid-run) and its late signals must bounce off the sender guards."""
    worker = _worker(eng)
    thread = SimpleNamespace(quit=lambda: None, wait=lambda ms: False)
    eng._worker, eng._thread = worker, thread

    eng.stop()
    assert eng._orphans == [(worker, thread)]
    assert worker._stop.is_set()

    changed = _spy(eng.state_changed)
    worker.event.emit(DOCKED)  # the orphan finally flushes a queued event
    assert eng.state.docked_market_id is None
    assert changed == []


def test_stop_before_start_still_persists(eng, saved):
    eng.stop()
    assert saved == [eng.state]


#  start() without a journal directory


def test_start_without_a_journal_dir_reports_and_stays_idle(eng, monkeypatch):
    monkeypatch.setattr(engine_module.core, "resolve_journal_dir", lambda cfg: None)
    statuses = _spy(eng.status_changed)
    changed = _spy(eng.state_changed)

    eng.start()

    assert "No Elite Dangerous journal folder" in statuses[0][0]
    assert len(changed) == 1
    assert eng._thread is None and eng._worker is None


#  manual EDDN sync


def test_sync_eddn_now_requires_uplink_and_journal_dir(eng, tmp_path):
    assert eng.sync_eddn_now() is None  # sharing off
    eng.uplink = _StubUplink()
    assert eng.sync_eddn_now() is None  # no journal dir resolved


def test_sync_eddn_now_reads_the_session_from_disk(eng, tmp_path):
    (tmp_path / "Journal.2026-07-06T000000.01.log").write_text(
        '{"event":"LoadGame"}\n', encoding="utf-8"
    )
    (tmp_path / "Market.json").write_text('{"MarketID": 42}', encoding="utf-8")
    eng.uplink = _StubUplink()
    eng.journal_dir = tmp_path

    assert eng.sync_eddn_now() == (True, False)
    events, market = eng.uplink.synced
    assert [e["event"] for e in events] == ["LoadGame"]
    assert market == {"MarketID": 42}
