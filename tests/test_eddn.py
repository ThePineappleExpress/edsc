import gzip
import io
import json
import time
import urllib.error
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

import pytest

from edsc.eddn import (
    COMMODITY_SCHEMA_REF,
    JOURNAL_SCHEMA_REF,
    ActivityEntry,
    EddnSender,
    EddnUplink,
    GameSession,
    LocationTracker,
    build_commodity_message,
    build_envelope,
    build_journal_message,
    format_activity,
)

NOW_TS = "2026-07-12T12:00:00Z"
NOW = datetime(2026, 7, 12, 12, 0, 0, tzinfo=timezone.utc).timestamp()

SCHEMA_DIR = Path(__file__).parent / "schemas"


def _validate(envelope, schema_file):
    """Validate an envelope against a bundled official EDDN schema."""
    jsonschema = pytest.importorskip("jsonschema")
    schema = json.loads((SCHEMA_DIR / schema_file).read_text(encoding="utf-8"))
    jsonschema.validate(envelope, schema)


def _session(**over):
    kw = {
        "gameversion": "4.1.0.0", "gamebuild": "r317908/r0 ", "horizons": True, "odyssey": True
    }
    kw.update(over)
    return GameSession(**kw)


def _location(**over):
    kw = {
        "star_system": "Col 285 Sector AB-C",
        "star_pos": (10.0, -3.5, 88.25),
        "system_address": 123456789,
        "docked_market_id": None,
    }
    kw.update(over)
    return LocationTracker(**kw)


def _docked(**over):
    ev = {
        "timestamp": NOW_TS,
        "event": "Docked",
        "StationName": "Trailblazer Dream",
        "StationType": "SurfaceStation",
        "StarSystem": "Col 285 Sector AB-C",
        "SystemAddress": 123456789,
        "MarketID": 4260000001,
        "DistFromStarLS": 220.4,
        "Wanted": True,
        "ActiveFine": True,
        "CockpitBreach": True,
        "StationEconomy": "$economy_Colony;",
        "StationEconomy_Localised": "Colony",
        "StationEconomies": [
            {"Name": "$economy_Colony;", "Name_Localised": "Colony", "Proportion": 1.0}
        ],
    }
    ev.update(over)
    return ev


def _fsdjump(**over):
    ev = {
        "timestamp": NOW_TS,
        "event": "FSDJump",
        "StarSystem": "Wolf 359",
        "SystemAddress": 10477373803,
        "StarPos": [3.79, 0.51, 0.15],
        "JumpDist": 15.2,
        "FuelUsed": 0.8,
        "FuelLevel": 12.0,
        "BoostUsed": True,
        "Wanted": False,
        "Factions": [
            {
                "Name": "Wolf 359 Corp",
                "FactionState": "None",
                "MyReputation": 100.0,
                "HomeSystem": True,
                "HappiestSystem": True,
                "SquadronFaction": False,
            }
        ],
    }
    ev.update(over)
    return ev


def _market(**over):
    m = {
        "timestamp": NOW_TS,
        "event": "Market",
        "MarketID": 4260000001,
        "StationName": "Trailblazer Dream",
        "StationType": "SurfaceStation",
        "StarSystem": "Col 285 Sector AB-C",
        "Items": [
            {
                "id": 128049202,
                "Name": "$steel_name;",
                "Name_Localised": "Steel",
                "Category": "$MARKET_category_metals;",
                "Category_Localised": "Metals",
                "BuyPrice": 0,
                "SellPrice": 4321,
                "MeanPrice": 4000,
                "StockBracket": 0,
                "DemandBracket": 3,
                "Stock": 0,
                "Demand": 12345,
                "Consumer": True,
                "Producer": False,
                "Rare": False,
            },
            {
                "id": 128049203,
                "Name": "$drones_name;",
                "Name_Localised": "Limpet",
                "Category": "$MARKET_category_nonmarketable;",
                "BuyPrice": 101,
                "SellPrice": 0,
                "MeanPrice": 100,
                "StockBracket": 3,
                "DemandBracket": 0,
                "Stock": 100,
                "Demand": 0,
            },
            {
                "id": 128049204,
                "Name": "$onionheadc_name;",
                "Category": "$MARKET_category_drugs;",
                "Legality": "IL",
                "BuyPrice": 0,
                "SellPrice": 500,
                "MeanPrice": 400,
                "StockBracket": 0,
                "DemandBracket": 1,
                "Stock": 0,
                "Demand": 5,
            },
        ],
    }
    m.update(over)
    return m


#  GameSession


def test_session_tracks_fileheader_and_loadgame():
    s = GameSession()
    s.apply_event({"event": "Fileheader", "gameversion": "4.1.0.0", "build": "r1 "})
    assert s.gameversion == "4.1.0.0"
    assert s.gamebuild == "r1 "  # whitespace kept: EDDN says do not strip
    assert s.horizons is None and s.odyssey is None
    s.apply_event({"event": "LoadGame", "Horizons": True, "Odyssey": False})
    assert s.horizons is True and s.odyssey is False


def test_session_fileheader_resets_expansion_flags():
    s = _session()
    s.apply_event({"event": "Fileheader", "gameversion": "4.1.0.0", "build": "r1"})
    assert s.horizons is None and s.odyssey is None


@pytest.mark.parametrize("version", ["4.0.0.100 beta", "Alpha 4", "1.2 BETA"])
def test_session_detects_beta(version):
    assert GameSession(gameversion=version).is_beta


def test_session_live_is_not_beta():
    assert not _session().is_beta
    assert not GameSession().is_beta  # unknown version: not flagged as beta


#  LocationTracker


def test_tracker_follows_jumps_and_docking():
    t = LocationTracker()
    t.apply_event(_fsdjump())
    assert t.star_system == "Wolf 359"
    assert t.star_pos == (3.79, 0.51, 0.15)
    assert t.system_address == 10477373803
    t.apply_event({"event": "Docked", "MarketID": 42})
    assert t.docked_market_id == 42
    t.apply_event({"event": "Undocked", "MarketID": 42})
    assert t.docked_market_id is None


def test_tracker_fsdjump_clears_docked_market():
    t = _location(docked_market_id=42)
    t.apply_event(_fsdjump())
    assert t.docked_market_id is None


def test_tracker_carrier_jump_keeps_docked_market():
    t = _location(docked_market_id=42)
    t.apply_event(
        {
            "event": "CarrierJump",
            "StarSystem": "Deciat",
            "SystemAddress": 6681123623626,
            "StarPos": [-9.4, -41.5, -4.2],
            "Docked": True,
        }
    )
    assert t.docked_market_id == 42
    assert t.star_system == "Deciat"


def test_tracker_location_event_sets_docked_state():
    t = LocationTracker()
    t.apply_event(
        {
            "event": "Location",
            "StarSystem": "Sol",
            "SystemAddress": 10477373803,
            "StarPos": [0.0, 0.0, 0.0],
            "Docked": True,
            "MarketID": 128016640,
        }
    )
    assert t.docked_market_id == 128016640


def test_tracker_matches_prefers_system_address():
    t = _location()
    assert t.matches({"SystemAddress": 123456789})
    assert not t.matches({"SystemAddress": 987654321})
    assert t.matches({"StarSystem": "col 285 sector ab-c"})
    assert not t.matches({"StarSystem": "Sol"})
    assert not LocationTracker().matches({"SystemAddress": 123456789})


#  journal builder


def test_journal_docked_augmented_and_sanitised():
    msg = build_journal_message(_docked(), _session(), _location(), now=NOW)
    assert msg is not None
    assert msg["StarPos"] == [10.0, -3.5, 88.25]
    for key in ("Wanted", "ActiveFine", "CockpitBreach"):
        assert key not in msg
    assert "StationEconomy_Localised" not in msg
    assert "Name_Localised" not in msg["StationEconomies"][0]
    assert msg["horizons"] is True and msg["odyssey"] is True
    envelope = build_envelope(JOURNAL_SCHEMA_REF, msg, _session(), "uid")
    _validate(envelope, "journal-v1.0.json")


def test_journal_docked_dropped_without_matching_location():
    assert (
        build_journal_message(
            _docked(), _session(), _location(system_address=1, star_system="Sol"),
            now=NOW,
        )
        is None
    )
    assert (
        build_journal_message(_docked(), _session(), LocationTracker(), now=NOW)
        is None
    )


def test_journal_fsdjump_needs_no_augmentation():
    msg = build_journal_message(_fsdjump(), _session(), LocationTracker(), now=NOW)
    assert msg is not None
    for key in ("JumpDist", "FuelUsed", "FuelLevel", "BoostUsed", "Wanted"):
        assert key not in msg
    faction = msg["Factions"][0]
    assert faction["Name"] == "Wolf 359 Corp"
    for key in ("MyReputation", "HomeSystem", "HappiestSystem", "SquadronFaction"):
        assert key not in faction
    envelope = build_envelope(JOURNAL_SCHEMA_REF, msg, _session(), "uid")
    _validate(envelope, "journal-v1.0.json")


def test_journal_location_strips_coordinates():
    ev = {
        "timestamp": NOW_TS,
        "event": "Location",
        "StarSystem": "Sol",
        "SystemAddress": 10477373803,
        "StarPos": [0.0, 0.0, 0.0],
        "Docked": False,
        "Latitude": 12.3,
        "Longitude": -45.6,
        "Wanted": False,
    }
    msg = build_journal_message(ev, _session(), LocationTracker(), now=NOW)
    assert msg is not None
    assert "Latitude" not in msg and "Longitude" not in msg
    envelope = build_envelope(JOURNAL_SCHEMA_REF, msg, _session(), "uid")
    _validate(envelope, "journal-v1.0.json")


def test_journal_expansion_flags_omitted_when_unknown():
    session = _session(horizons=None, odyssey=None)
    msg = build_journal_message(_fsdjump(), session, LocationTracker(), now=NOW)
    assert "horizons" not in msg and "odyssey" not in msg
    envelope = build_envelope(JOURNAL_SCHEMA_REF, msg, session, "uid")
    _validate(envelope, "journal-v1.0.json")


def test_journal_rejects_stale_beta_and_unsupported():
    ok = {"session": _session(), "location": _location()}
    # replayed history (an hour old) and clock-skewed future events
    assert build_journal_message(_docked(), now=NOW + 3600, **ok) is None
    assert build_journal_message(_docked(), now=NOW - 3600, **ok) is None
    assert (
        build_journal_message(
            _docked(), _session(gameversion="4.0 beta"), _location(), now=NOW
        )
        is None
    )
    undocked = {"timestamp": NOW_TS, "event": "Undocked", "MarketID": 1}
    assert build_journal_message(undocked, _session(), _location(), now=NOW) is None
    assert (
        build_journal_message(_docked(timestamp="garbage"), _session(), _location())
        is None
    )


#  commodity builder


def test_commodity_message_maps_filters_and_validates():
    location = _location(docked_market_id=4260000001)
    msg = build_commodity_message(_market(), _session(), location, now=NOW)
    assert msg is not None
    assert msg["systemName"] == "Col 285 Sector AB-C"
    assert msg["stationName"] == "Trailblazer Dream"
    assert msg["marketId"] == 4260000001
    assert msg["stationType"] == "SurfaceStation"
    # limpets (NonMarketable) and illegal goods are filtered out
    assert [c["name"] for c in msg["commodities"]] == ["steel"]
    steel = msg["commodities"][0]
    assert steel == {
        "name": "steel",
        "meanPrice": 4000,
        "buyPrice": 0,
        "stock": 0,
        "stockBracket": 0,
        "sellPrice": 4321,
        "demand": 12345,
        "demandBracket": 3,
    }
    envelope = build_envelope(COMMODITY_SCHEMA_REF, msg, _session(), "uid")
    _validate(envelope, "commodity-v3.0.json")


def test_commodity_requires_docked_at_that_market():
    assert build_commodity_message(_market(), _session(), _location(), now=NOW) is None
    other = _location(docked_market_id=999)
    assert build_commodity_message(_market(), _session(), other, now=NOW) is None


def test_commodity_rejects_stale_and_beta():
    location = _location(docked_market_id=4260000001)
    assert build_commodity_message(_market(), _session(), location, now=NOW + 3600) is None
    beta = _session(gameversion="4.0 beta")
    assert build_commodity_message(_market(), beta, location, now=NOW) is None


def test_commodity_empty_market_is_still_a_message():
    # A fleet carrier with all orders cleared genuinely has no commodities.
    location = _location(docked_market_id=4260000001)
    msg = build_commodity_message(_market(Items=[]), _session(), location, now=NOW)
    assert msg is not None and msg["commodities"] == []
    envelope = build_envelope(COMMODITY_SCHEMA_REF, msg, _session(), "uid")
    _validate(envelope, "commodity-v3.0.json")


def test_commodity_bracket_passthrough():
    # "" is a legal levelType: temporarily traded goods
    items = [
        {
            "Name": "$gold_name;",
            "Category": "$MARKET_category_metals;",
            "BuyPrice": 0,
            "SellPrice": 50000,
            "MeanPrice": 47000,
            "StockBracket": "",
            "DemandBracket": 2,
            "Stock": 0,
            "Demand": 100,
        }
    ]
    location = _location(docked_market_id=4260000001)
    msg = build_commodity_message(
        _market(Items=items), _session(), location, now=NOW
    )
    assert msg["commodities"][0]["stockBracket"] == ""
    envelope = build_envelope(COMMODITY_SCHEMA_REF, msg, _session(), "uid")
    _validate(envelope, "commodity-v3.0.json")


#  envelope


def test_envelope_header():
    env = build_envelope(
        JOURNAL_SCHEMA_REF, {"x": 1}, _session(), "some-uuid", software_version="9.9"
    )
    assert env["$schemaRef"] == JOURNAL_SCHEMA_REF
    assert env["header"] == {
        "uploaderID": "some-uuid",
        "softwareName": "EDSC",
        "softwareVersion": "9.9",
        "gameversion": "4.1.0.0",
        "gamebuild": "r317908/r0 ",
    }
    assert env["message"] == {"x": 1}


#  sender


class _FakeGateway:
    """urlopen stand-in; yields OK, or pops one canned exception per call."""

    def __init__(self, responses=()):
        self.requests = []
        self.responses = list(responses)

    @contextmanager
    def __call__(self, req, timeout=0):
        self.requests.append(req)
        if self.responses:
            response = self.responses.pop(0)
            if isinstance(response, Exception):
                raise response
        yield io.BytesIO(b"OK")


def _wait_for(predicate, timeout=5.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if predicate():
            return True
        time.sleep(0.01)
    return False


def _http_error(code):
    return urllib.error.HTTPError("https://x/", code, "boom", {}, None)


def test_sender_posts_gzipped_envelope():
    gateway = _FakeGateway()
    sender = EddnSender(
        "uid", software_version="9.9", gateway_url="https://gw/", opener=gateway
    )
    assert sender.submit(JOURNAL_SCHEMA_REF, {"x": 1}, _session())
    assert _wait_for(lambda: sender.sent == 1)
    sender.close()
    (req,) = gateway.requests
    assert req.full_url == "https://gw/"
    assert req.get_header("Content-encoding") == "gzip"
    envelope = json.loads(gzip.decompress(req.data).decode("utf-8"))
    assert envelope["$schemaRef"] == JOURNAL_SCHEMA_REF
    assert envelope["header"]["uploaderID"] == "uid"
    assert envelope["message"] == {"x": 1}


def test_sender_test_mode_appends_test_suffix():
    gateway = _FakeGateway()
    sender = EddnSender("uid", opener=gateway, use_test_schemas=True)
    sender.submit(COMMODITY_SCHEMA_REF, {"x": 1}, _session())
    assert _wait_for(lambda: sender.sent == 1)
    sender.close()
    envelope = json.loads(gzip.decompress(gateway.requests[0].data).decode("utf-8"))
    assert envelope["$schemaRef"] == COMMODITY_SCHEMA_REF + "/test"


def test_sender_never_retries_schema_rejection():
    gateway = _FakeGateway(responses=[_http_error(400)])
    sender = EddnSender("uid", opener=gateway, retry_delays=(0.01, 0.01))
    sender.submit(JOURNAL_SCHEMA_REF, {"x": 1}, _session())
    assert _wait_for(lambda: sender.rejected == 1)
    sender.close()
    assert len(gateway.requests) == 1
    assert sender.sent == 0
    assert sender.last_error == "HTTP 400"


def test_sender_retries_transient_errors_then_succeeds():
    gateway = _FakeGateway(
        responses=[urllib.error.URLError("down"), _http_error(503)]
    )
    sender = EddnSender("uid", opener=gateway, retry_delays=(0.01, 0.01))
    sender.submit(JOURNAL_SCHEMA_REF, {"x": 1}, _session())
    assert _wait_for(lambda: sender.sent == 1)
    sender.close()
    assert len(gateway.requests) == 3


def test_sender_gives_up_after_retries_exhausted():
    gateway = _FakeGateway(
        responses=[urllib.error.URLError("down")] * 3
    )
    sender = EddnSender("uid", opener=gateway, retry_delays=(0.01, 0.01))
    sender.submit(JOURNAL_SCHEMA_REF, {"x": 1}, _session())
    assert _wait_for(lambda: sender.failed == 1)
    sender.close()
    assert len(gateway.requests) == 3
    assert sender.sent == 0


def test_sender_drops_when_queue_full(monkeypatch):
    sender = EddnSender("uid", opener=_FakeGateway(), max_queue=1)
    monkeypatch.setattr(sender, "_ensure_thread", lambda: None)
    assert sender.submit(JOURNAL_SCHEMA_REF, {"x": 1}, _session())
    assert not sender.submit(JOURNAL_SCHEMA_REF, {"x": 2}, _session())
    assert sender.dropped == 1


def test_sender_close_aborts_pending_retry_quickly():
    gateway = _FakeGateway(responses=[urllib.error.URLError("down")] * 9)
    sender = EddnSender("uid", opener=gateway, retry_delays=(60.0,))
    sender.submit(JOURNAL_SCHEMA_REF, {"x": 1}, _session())
    assert _wait_for(lambda: gateway.requests)  # worker is now in retry wait
    start = time.time()
    sender.close(timeout=5.0)
    # Well under the close timeout: an abort takes milliseconds, and a bound equal to the timeout couldn't tell an abort from a stuck join.
    assert time.time() - start < 2.0
    assert not sender._thread.is_alive()
    assert not sender.submit(JOURNAL_SCHEMA_REF, {"x": 2}, _session())


#  activity log


def test_activity_records_a_sent_entry_with_a_label():
    gateway = _FakeGateway()
    sender = EddnSender("uid", opener=gateway)
    sender.submit(
        JOURNAL_SCHEMA_REF,
        {"event": "Docked", "StarSystem": "Sol", "StationName": "Abraham Lincoln"},
        _session(),
    )
    assert _wait_for(lambda: sender.sent == 1)
    sender.close()
    (entry,) = sender.activity.snapshot()
    assert entry.kind == "journal"
    assert entry.label == "Docked · Sol / Abraham Lincoln"
    assert entry.outcome == "sent"
    assert entry.detail == ""


def test_activity_records_rejection_detail():
    gateway = _FakeGateway(responses=[_http_error(400)])
    sender = EddnSender("uid", opener=gateway, retry_delays=(0.01, 0.01))
    sender.submit(COMMODITY_SCHEMA_REF, {"systemName": "Sol", "stationName": "X",
                                         "commodities": [1, 2]}, _session())
    assert _wait_for(lambda: sender.rejected == 1)
    sender.close()
    (entry,) = sender.activity.snapshot()
    assert entry.kind == "commodity"
    assert entry.label == "Market · Sol / X · 2 goods"
    assert entry.outcome == "rejected"
    assert entry.detail == "HTTP 400"


def test_activity_marks_dropped_when_queue_full(monkeypatch):
    sender = EddnSender("uid", opener=_FakeGateway(), max_queue=1)
    monkeypatch.setattr(sender, "_ensure_thread", lambda: None)
    sender.submit(JOURNAL_SCHEMA_REF, {"event": "FSDJump", "StarSystem": "A"},
                  _session())
    sender.submit(JOURNAL_SCHEMA_REF, {"event": "FSDJump", "StarSystem": "B"},
                  _session())
    outcomes = [e.outcome for e in sender.activity.snapshot()]
    assert outcomes == ["queued", "dropped"]


def test_activity_log_is_bounded():
    sender = EddnSender("uid", opener=_FakeGateway(), max_queue=1, activity_maxlen=3)
    sender._ensure_thread = lambda: None  # keep the worker idle
    for i in range(5):
        # Fill the single queue slot once, then every further submit drops but still records an entry -- the log must never exceed its cap.
        sender.submit(JOURNAL_SCHEMA_REF, {"event": "FSDJump", "StarSystem": str(i)},
                      _session())
    assert len(sender.activity.snapshot()) == 3


def test_format_activity_renders_symbol_and_detail():
    entry = ActivityEntry(time=0.0, kind="journal", label="Docked · Sol",
                          outcome="rejected", detail="HTTP 400")
    line = format_activity(entry)
    assert "✗" in line
    assert line.endswith("Docked · Sol — HTTP 400")


#  uplink facade


class _StubSender:
    def __init__(self):
        self.calls = []

    def submit(self, schema_ref, message, session):
        self.calls.append((schema_ref, message))
        return True


def _live_ts():
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def test_uplink_tracks_but_does_not_send_until_armed():
    stub = _StubSender()
    uplink = EddnUplink(stub)
    uplink.handle_event(
        {"event": "Fileheader", "gameversion": "4.1.0.0", "build": "r1"}
    )
    uplink.handle_event({"event": "LoadGame", "Horizons": True, "Odyssey": True})
    uplink.handle_event(_fsdjump(timestamp=_live_ts()))
    assert stub.calls == []  # replayed history must never be uploaded
    assert uplink.session.gameversion == "4.1.0.0"
    assert uplink.location.star_system == "Wolf 359"

    uplink.arm()
    assert uplink.handle_event(
        _fsdjump(timestamp=_live_ts(), StarSystem="Duamta", SystemAddress=123)
    )
    (call,) = stub.calls
    assert call[0] == JOURNAL_SCHEMA_REF
    assert call[1]["StarSystem"] == "Duamta"


def test_uplink_disabled_sends_nothing():
    stub = _StubSender()
    uplink = EddnUplink(stub)
    uplink.arm()
    uplink.enabled = False
    assert not uplink.handle_event(_fsdjump(timestamp=_live_ts()))
    assert stub.calls == []


def test_uplink_market_deduplicates_unchanged_snapshots():
    stub = _StubSender()
    uplink = EddnUplink(stub)
    uplink.arm()
    ts = _live_ts()
    uplink.handle_event(_fsdjump(timestamp=ts, StarSystem="Col 285 Sector AB-C",
                                 SystemAddress=123456789))
    uplink.handle_event(_docked(timestamp=ts))

    assert uplink.handle_market(_market(timestamp=ts))
    # Same content rewritten (commodities screen reopened): not resent.
    assert not uplink.handle_market(_market(timestamp=_live_ts()))
    # Changed stock: resent.
    changed = _market(timestamp=_live_ts())
    changed["Items"][0]["Demand"] = 1
    assert uplink.handle_market(changed)
    assert [ref for ref, _ in stub.calls] == [
        JOURNAL_SCHEMA_REF,  # the FSDJump
        JOURNAL_SCHEMA_REF,  # the Docked event
        COMMODITY_SCHEMA_REF,
        COMMODITY_SCHEMA_REF,
    ]


def test_uplink_market_ignored_when_not_docked_there():
    stub = _StubSender()
    uplink = EddnUplink(stub)
    uplink.arm()
    # No Docked event seen: a stale Market.json from a previous session.
    assert not uplink.handle_market(_market(timestamp=_live_ts()))
    assert stub.calls == []


#  freshness bypass (manual sync)


def test_journal_message_stale_dropped_but_kept_without_fresh_gate():
    far = NOW + 10_000  # well past the 300s freshness window
    ev = _fsdjump(timestamp=NOW_TS)
    assert build_journal_message(ev, _session(), _location(), now=far) is None
    msg = build_journal_message(
        ev, _session(), _location(), now=far, require_fresh=False
    )
    assert msg is not None and msg["StarSystem"] == "Wolf 359"


def test_commodity_message_stale_dropped_but_kept_without_fresh_gate():
    loc = _location(docked_market_id=4260000001)
    far = NOW + 10_000
    assert build_commodity_message(_market(timestamp=NOW_TS), _session(), loc, now=far) is None
    msg = build_commodity_message(
        _market(timestamp=NOW_TS), _session(), loc, now=far, require_fresh=False
    )
    assert msg is not None and msg["marketId"] == 4260000001


def test_commodity_message_manual_still_requires_docked_there():
    # Bypassing freshness must not bypass the docked cross-check: a market you aren't docked at is never relayed, however the push was triggered.
    loc = _location(docked_market_id=999)
    assert build_commodity_message(
        _market(timestamp=NOW_TS), _session(), loc, now=NOW, require_fresh=False
    ) is None


#  manual sync


def _session_events():
    """A current-session journal in order: header, load, jump, dock."""
    return [
        {"event": "Fileheader", "gameversion": "4.1.0.0", "build": "r1 "},
        {"event": "LoadGame", "Horizons": True, "Odyssey": True},
        _fsdjump(
            timestamp=NOW_TS,
            StarSystem="Col 285 Sector AB-C",
            SystemAddress=123456789,
        ),
        _docked(timestamp=NOW_TS),
    ]


def test_sync_now_pushes_stale_session_from_cold_uplink():
    stub = _StubSender()
    uplink = EddnUplink(stub)
    # Never armed, never saw a live event: the start-docked case where the automatic path would upload nothing until the next jump/dock.
    journal_sent, market_sent = uplink.sync_now(
        _session_events(), _market(timestamp=NOW_TS)
    )
    assert journal_sent and market_sent
    refs = [ref for ref, _ in stub.calls]
    assert JOURNAL_SCHEMA_REF in refs and COMMODITY_SCHEMA_REF in refs
    # Trackers were warmed from the replayed session.
    assert uplink.session.gameversion == "4.1.0.0"
    assert uplink.location.docked_market_id == 4260000001
    # The Docked event (latest position) is the one relayed, augmented with the StarPos carried from the jump.
    journal_msg = next(m for r, m in stub.calls if r == JOURNAL_SCHEMA_REF)
    assert journal_msg["event"] == "Docked"
    assert journal_msg["StarPos"] == [3.79, 0.51, 0.15]


def test_sync_now_updates_dedup_key():
    stub = _StubSender()
    uplink = EddnUplink(stub)
    uplink.arm()
    uplink.sync_now(_session_events(), _market(timestamp=NOW_TS))
    sent = len(stub.calls)
    # The automatic path must not resend the identical snapshot just pushed.
    assert not uplink.handle_market(_market(timestamp=_live_ts()))
    assert len(stub.calls) == sent


def test_sync_now_disabled_does_nothing():
    stub = _StubSender()
    uplink = EddnUplink(stub)
    uplink.enabled = False
    assert uplink.sync_now(_session_events(), _market(timestamp=NOW_TS)) == (False, False)
    assert stub.calls == []


def test_sync_now_skips_market_when_no_longer_docked():
    stub = _StubSender()
    uplink = EddnUplink(stub)
    events = [
        *_session_events(),
        {"timestamp": NOW_TS, "event": "Undocked", "MarketID": 4260000001},
    ]
    journal_sent, market_sent = uplink.sync_now(events, _market(timestamp=NOW_TS))
    assert journal_sent  # position is still shareable
    assert not market_sent  # not docked there any more
    assert uplink.location.docked_market_id is None


def test_sync_now_without_market_still_shares_position():
    stub = _StubSender()
    uplink = EddnUplink(stub)
    journal_sent, market_sent = uplink.sync_now(_session_events(), None)
    assert journal_sent and not market_sent
