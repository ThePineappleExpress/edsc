"""Build and submit journal and commodity messages to EDDN."""

# SPDX-License-Identifier: GPL-3.0-or-later

from __future__ import annotations

import gzip
import json
import queue
import threading
import time
import urllib.error
import urllib.request
from collections import deque
from collections.abc import Callable, Iterable
from contextlib import suppress
from dataclasses import dataclass
from typing import Any

from . import __version__
from .time_utils import parse_timestamp

GATEWAY_URL = "https://eddn.edcd.io:4430/upload/"
SOFTWARE_NAME = "EDSC"

JOURNAL_SCHEMA_REF = "https://eddn.edcd.io/schemas/journal/1"
COMMODITY_SCHEMA_REF = "https://eddn.edcd.io/schemas/commodity/3"

# Journal events relayed through the journal/1 schema (Scan/SAASignalsFound are also accepted there, but EDSC has no exploration data worth relaying).
JOURNAL_EVENTS = frozenset({"Docked", "FSDJump", "Location", "CarrierJump"})

# Events older than this are never uploaded: anything that stale can only come from journal replay or a leftover snapshot, not live play; the second line of defence (the first: the uplink stays disarmed until bootstrap finishes replaying).
MAX_MESSAGE_AGE_S = 300.0

# Personal/transient keys the journal/1 schema marks as disallowed.
_DISALLOWED_KEYS = frozenset({
    "ActiveFine", "CockpitBreach", "BoostUsed", "FuelLevel", "FuelUsed",
    "JumpDist", "Latitude", "Longitude", "Wanted", "IsNewEntry",
    "NewTraitsDiscovered", "Traits", "VoucherAmount",
})
_DISALLOWED_FACTION_KEYS = frozenset({
    "HappiestSystem", "HomeSystem", "MyReputation", "SquadronFaction",
})

# Gateway verdicts that can never succeed on retry: 400 (schema violation, i.e. a builder bug) and 426 (schema version outdated); EDDN forbids retrying these.
_PERMANENT_HTTP_CODES = (400, 426)

_SENTINEL = object()


def _is_fresh(ts: Any, now: float | None) -> bool:
    timestamp = parse_timestamp(ts)
    if timestamp is None:
        return False
    if now is None:
        now = time.time()
    return abs(now - timestamp.timestamp()) <= MAX_MESSAGE_AGE_S


#  session / location tracking


@dataclass
class GameSession:
    """gameversion/gamebuild and expansion flags, from Fileheader/LoadGame; EDDN requires gameversion/gamebuild in every header (to split Live/Legacy galaxies) and horizons/odyssey in bodies, and gamebuild is kept verbatim (EDDN says not to strip whitespace)."""

    gameversion: str = ""
    gamebuild: str = ""
    horizons: bool | None = None
    odyssey: bool | None = None

    def apply_event(self, event: dict[str, Any]) -> None:
        etype = event.get("event")
        if etype == "Fileheader":
            self.gameversion = str(event.get("gameversion") or "")
            self.gamebuild = str(event.get("build") or "")
            # Expansion flags describe a commander, not the client; re-learn them from the LoadGame that follows.
            self.horizons = None
            self.odyssey = None
        elif etype == "LoadGame":
            if event.get("gameversion"):
                self.gameversion = str(event["gameversion"])
            if event.get("build"):
                self.gamebuild = str(event["build"])
            if "Horizons" in event:
                self.horizons = bool(event["Horizons"])
            if "Odyssey" in event:
                self.odyssey = bool(event["Odyssey"])

    @property
    def is_beta(self) -> bool:
        """Alpha/beta clients must never feed the live schemas."""
        v = self.gameversion.lower()
        return "beta" in v or "alpha" in v


@dataclass
class LocationTracker:
    """Last known system position and docked market, for augmentation; journal/1 requires StarSystem/StarPos/SystemAddress in every message, but Docked lacks StarPos, so it's filled from the last jump/Location -- only when the tracked system provably matches the event (the schema's "location cross-check")."""

    star_system: str = ""
    star_pos: tuple[float, float, float] | None = None
    system_address: int | None = None
    docked_market_id: int | None = None

    def apply_event(self, event: dict[str, Any]) -> None:
        etype = event.get("event")
        if etype in ("FSDJump", "CarrierJump", "Location"):
            pos = event.get("StarPos")
            if isinstance(pos, (list, tuple)) and len(pos) == 3:
                try:
                    self.star_pos = tuple(float(c) for c in pos)
                except (TypeError, ValueError):
                    return
                self.star_system = str(event.get("StarSystem") or "")
                addr = event.get("SystemAddress")
                self.system_address = addr if isinstance(addr, int) else None
            if etype == "FSDJump":
                self.docked_market_id = None
            elif etype == "Location":
                mid = event.get("MarketID")
                docked = bool(event.get("Docked")) and isinstance(mid, int)
                self.docked_market_id = mid if docked else None
            # CarrierJump: still docked on the carrier that moved; keep it.
        elif etype == "Docked":
            mid = event.get("MarketID")
            if isinstance(mid, int):
                self.docked_market_id = mid
        elif etype == "Undocked":
            self.docked_market_id = None

    def matches(self, event: dict[str, Any]) -> bool:
        """True when the tracked position verifiably is the event's system."""
        if self.star_pos is None:
            return False
        addr = event.get("SystemAddress")
        if isinstance(addr, int) and self.system_address is not None:
            return addr == self.system_address
        system = event.get("StarSystem")
        if isinstance(system, str) and system and self.star_system:
            return system.lower() == self.star_system.lower()
        return False


#  message builders (pure functions)


def _strip_localised(value: Any) -> Any:
    """Drop every ``*_Localised`` key, at any depth (schema requirement)."""
    if isinstance(value, dict):
        return {
            k: _strip_localised(v)
            for k, v in value.items()
            if not k.endswith("_Localised")
        }
    if isinstance(value, list):
        return [_strip_localised(v) for v in value]
    return value


def build_journal_message(
    event: dict[str, Any],
    session: GameSession,
    location: LocationTracker,
    now: float | None = None,
    require_fresh: bool = True,
) -> dict[str, Any] | None:
    """Sanitised, augmented journal/1 message body, or None to skip; ``require_fresh`` drops the age gate for an explicit manual sync (the player asserting they're here now), but the location cross-check and field validation still stand, so a message is only built for the current, known position."""
    if event.get("event") not in JOURNAL_EVENTS:
        return None
    if session.is_beta:
        return None
    if require_fresh and not _is_fresh(event.get("timestamp"), now):
        return None

    msg = _strip_localised(event)
    for key in _DISALLOWED_KEYS:
        msg.pop(key, None)
    factions = msg.get("Factions")
    if isinstance(factions, list):
        for faction in factions:
            if isinstance(faction, dict):
                for key in _DISALLOWED_FACTION_KEYS:
                    faction.pop(key, None)

    # Docked carries no StarPos; fill it in only when the tracked position demonstrably belongs to the event's own system.
    if "StarPos" not in msg:
        if not location.matches(msg):
            return None
        msg["StarPos"] = list(location.star_pos)

    if not isinstance(msg.get("StarSystem"), str) or not msg["StarSystem"]:
        return None
    if not isinstance(msg.get("SystemAddress"), int):
        return None
    pos = msg.get("StarPos")
    if (
        not isinstance(pos, list)
        or len(pos) != 3
        or not all(isinstance(c, (int, float)) for c in pos)
    ):
        return None

    if session.horizons is not None:
        msg["horizons"] = session.horizons
    if session.odyssey is not None:
        msg["odyssey"] = session.odyssey
    return msg


def _commodity_symbol(name: Any) -> str:
    """``$gold_name;`` -> ``gold`` (the symbolic name EDDN expects)."""
    if not isinstance(name, str):
        return ""
    return name.replace("$", "").replace("_name;", "")


def _category_key(category: Any) -> str:
    """Normalise ``$MARKET_category_metals;``/``Metals`` for comparisons."""
    if not isinstance(category, str):
        return ""
    return category.replace("$MARKET_category_", "").rstrip(";").lower()


def _bracket(value: Any) -> int | str:
    # levelType: 0-3, or "" meaning "temporarily traded here only".
    if value in (0, 1, 2, 3, ""):
        return value
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def build_commodity_message(
    market: dict[str, Any],
    session: GameSession,
    location: LocationTracker,
    now: float | None = None,
    require_fresh: bool = True,
) -> dict[str, Any] | None:
    """commodity/3 message body from a Market.json snapshot, or None to skip; the snapshot is trusted only while docked at the very market it describes (Market.json survives long after undocking, so anything else is stale), an empty commodity list still sent (for a carrier that means "nothing traded here any more"); ``require_fresh`` drops the age gate for a manual sync but keeps the docked-market cross-check."""
    mid = market.get("MarketID")
    if not isinstance(mid, int) or location.docked_market_id != mid:
        return None
    if session.is_beta:
        return None
    if require_fresh and not _is_fresh(market.get("timestamp"), now):
        return None
    system = market.get("StarSystem") or ""
    station = market.get("StationName") or ""
    if not system or not station:
        return None

    commodities: list[dict[str, Any]] = []
    for item in market.get("Items") or []:
        if not isinstance(item, dict):
            continue
        # Schema README: omit NonMarketable (limpets) and illegal-here goods.
        if _category_key(item.get("Category")) == "nonmarketable":
            continue
        if item.get("Legality"):
            continue
        name = _commodity_symbol(item.get("Name"))
        if not name:
            continue
        commodities.append({
            "name": name,
            "meanPrice": int(item.get("MeanPrice") or 0),
            "buyPrice": int(item.get("BuyPrice") or 0),
            "stock": int(item.get("Stock") or 0),
            "stockBracket": _bracket(item.get("StockBracket")),
            "sellPrice": int(item.get("SellPrice") or 0),
            "demand": int(item.get("Demand") or 0),
            "demandBracket": _bracket(item.get("DemandBracket")),
        })

    msg: dict[str, Any] = {
        "systemName": system,
        "stationName": station,
        "marketId": mid,
        "timestamp": market.get("timestamp"),
        "commodities": commodities,
    }
    if market.get("StationType"):
        msg["stationType"] = str(market["StationType"])
    if session.horizons is not None:
        msg["horizons"] = session.horizons
    if session.odyssey is not None:
        msg["odyssey"] = session.odyssey
    return msg


def build_envelope(
    schema_ref: str,
    message: dict[str, Any],
    session: GameSession,
    uploader_id: str,
    software_version: str = __version__,
) -> dict[str, Any]:
    """Wrap a message in the EDDN upload envelope."""
    return {
        "$schemaRef": schema_ref,
        "header": {
            "uploaderID": uploader_id,
            "softwareName": SOFTWARE_NAME,
            "softwareVersion": software_version,
            "gameversion": session.gameversion,
            "gamebuild": session.gamebuild,
        },
        "message": message,
    }


#  activity log


@dataclass
class ActivityEntry:
    """One upload attempt as it moves through the sender, for the status UI; ``outcome`` starts at ``queued`` and is advanced in place by the worker thread once delivery settles, so a live console reflects the final verdict without reconciling counters against messages."""

    time: float
    kind: str  # "journal" | "commodity"
    label: str
    outcome: str = "queued"  # queued -> sent | rejected | failed | dropped
    detail: str = ""

    def mark(self, outcome: str, detail: str = "") -> None:
        self.outcome = outcome
        if detail:
            self.detail = detail


_ACTIVITY_SYMBOLS = {
    "queued": "…",
    "sent": "✓",
    "rejected": "✗",
    "failed": "✗",
    "dropped": "⊘",
}


def format_activity(entry: ActivityEntry) -> str:
    """One console line, e.g. ``12:04:38 ✓ Docked · Sol / Abraham Lincoln``."""
    stamp = time.strftime("%H:%M:%S", time.localtime(entry.time))
    symbol = _ACTIVITY_SYMBOLS.get(entry.outcome, "·")
    detail = f" — {entry.detail}" if entry.detail else ""
    return f"{stamp} {symbol} {entry.label}{detail}"


def _activity_context(schema_ref: str, message: dict[str, Any]) -> tuple[str, str]:
    """Derive a ``(kind, label)`` activity summary from a message body."""
    if schema_ref.startswith(COMMODITY_SCHEMA_REF):
        system = message.get("systemName") or "?"
        station = message.get("stationName") or "?"
        count = len(message.get("commodities") or [])
        return "commodity", f"Market · {system} / {station} · {count} goods"
    label = str(message.get("event") or "Journal")
    system = message.get("StarSystem")
    station = message.get("StationName")
    if isinstance(system, str) and system:
        label += f" · {system}"
    if isinstance(station, str) and station:
        label += f" / {station}"
    return "journal", label


class ActivityLog:
    """A bounded, thread-safe ring of recent upload attempts; written from the journal thread (:meth:`record`) and mutated in place from the sender's worker thread, with :meth:`snapshot` handing the GUI thread an immutable copy so a status console renders without locking on every row."""

    def __init__(self, maxlen: int = 40) -> None:
        self._entries: deque[ActivityEntry] = deque(maxlen=maxlen)
        self._lock = threading.Lock()

    def record(self, kind: str, label: str) -> ActivityEntry:
        entry = ActivityEntry(time.time(), kind, label)
        with self._lock:
            self._entries.append(entry)
        return entry

    def snapshot(self) -> list[ActivityEntry]:
        """A newest-last copy of the log for the status UI."""
        with self._lock:
            return list(self._entries)


#  transport


class EddnSender:
    """Queued, threaded uploader: submit() never blocks the journal watcher; a daemon worker gzips and POSTs envelopes one at a time, transient failures waiting out EDDN's minimum one-minute retry (abandoned early on close()), 400/426 never retried. Counters are informational, for a future status UI."""

    def __init__(
        self,
        uploader_id: str,
        software_version: str = __version__,
        gateway_url: str = GATEWAY_URL,
        use_test_schemas: bool = False,
        max_queue: int = 100,
        timeout: float = 20.0,
        retry_delays: tuple[float, ...] = (60.0, 300.0),
        opener: Callable = urllib.request.urlopen,
        activity_maxlen: int = 40,
    ) -> None:
        self.uploader_id = uploader_id
        self.software_version = software_version
        self.gateway_url = gateway_url
        self.use_test_schemas = use_test_schemas
        self.timeout = timeout
        self._retry_delays = retry_delays
        self._opener = opener

        self._queue: queue.Queue = queue.Queue(maxsize=max_queue)
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._thread_lock = threading.Lock()

        self.sent = 0  # delivered to the gateway
        self.rejected = 0  # 400/426: our message was invalid, not retried
        self.failed = 0  # transient errors exhausted all retries
        self.dropped = 0  # queue full, never attempted
        self.last_error = ""
        # A rolling record of individual attempts backing the settings console; the running counters above are its aggregate summary.
        self.activity = ActivityLog(activity_maxlen)

    def status_line(self) -> str:
        """One-line human summary of this session's uploads, for the status UI."""
        parts = [f"sent {self.sent}"]
        for label, value in (
            ("rejected", self.rejected),
            ("failed", self.failed),
            ("dropped", self.dropped),
        ):
            if value:
                parts.append(f"{label} {value}")
        line = " · ".join(parts)
        if self.last_error:
            line += f" — last error: {self.last_error}"
        return line

    def submit(
        self, schema_ref: str, message: dict[str, Any], session: GameSession
    ) -> bool:
        """Enqueue one message for delivery; False if it was not queued."""
        if self._stop.is_set():
            return False
        if self.use_test_schemas:
            schema_ref += "/test"
        envelope = build_envelope(
            schema_ref, message, session, self.uploader_id, self.software_version
        )
        entry = self.activity.record(*_activity_context(schema_ref, message))
        try:
            self._queue.put_nowait((envelope, entry))
        except queue.Full:
            entry.mark("dropped")
            self.dropped += 1
            return False
        self._ensure_thread()
        return True

    def close(self, timeout: float = 5.0) -> None:
        """Stop the worker; pending and mid-retry messages are abandoned."""
        self._stop.set()
        with self._thread_lock:
            thread = self._thread
        if thread is None:
            return
        # Unblock a worker parked on get(); make room if the queue is full.
        while True:
            try:
                self._queue.put_nowait(_SENTINEL)
                break
            except queue.Full:
                with suppress(queue.Empty):
                    self._queue.get_nowait()
        thread.join(timeout)

    #  internals

    def _ensure_thread(self) -> None:
        with self._thread_lock:
            if self._thread is None or not self._thread.is_alive():
                self._thread = threading.Thread(
                    target=self._run, name="eddn-sender", daemon=True
                )
                self._thread.start()

    def _run(self) -> None:
        while True:
            item = self._queue.get()
            if item is _SENTINEL:
                return
            if self._stop.is_set():
                continue  # closing: discard the backlog quickly
            envelope, entry = item
            self._deliver(envelope, entry)

    def _deliver(self, envelope: dict[str, Any], entry: ActivityEntry) -> bool:
        attempts = 1 + len(self._retry_delays)
        for attempt in range(attempts):
            if attempt and self._stop.wait(self._retry_delays[attempt - 1]):
                return False
            try:
                self._post(envelope)
            except urllib.error.HTTPError as exc:
                self.last_error = f"HTTP {exc.code}"
                if exc.code in _PERMANENT_HTTP_CODES:
                    self.rejected += 1
                    entry.mark("rejected", f"HTTP {exc.code}")
                    return False
                continue
            except OSError as exc:  # URLError, timeouts, DNS failures
                self.last_error = str(exc)
                continue
            self.sent += 1
            entry.mark("sent")
            return True
        self.failed += 1
        entry.mark("failed", self.last_error)
        return False

    def _post(self, envelope: dict[str, Any]) -> None:
        body = gzip.compress(json.dumps(envelope).encode("utf-8"))
        req = urllib.request.Request(
            self.gateway_url,
            data=body,
            headers={
                "Content-Type": "application/json; charset=utf-8",
                "Content-Encoding": "gzip",
            },
            method="POST",
        )
        with self._opener(req, timeout=self.timeout) as resp:
            resp.read()


#  facade


def _market_key(msg: dict[str, Any]) -> tuple[int, str]:
    """Content fingerprint used to skip re-sending an unchanged market snapshot."""
    return (msg["marketId"], json.dumps(msg["commodities"], sort_keys=True))


class EddnUplink:
    """Routes watcher callbacks to EDDN once armed; the session/location trackers consume every event -- including replayed history, so headers/augmentations are correct from the first live event -- but nothing submits until arm() is called after bootstrap, the builders' freshness gate backstopping that (even a mis-wired replay can't upload stale data)."""

    def __init__(self, sender: EddnSender) -> None:
        self.sender = sender
        self.session = GameSession()
        self.location = LocationTracker()
        self.enabled = True
        self._armed = False
        self._last_market_key: tuple[int, str] | None = None

    def arm(self) -> None:
        """Allow submissions; call once bootstrap/replay has finished."""
        self._armed = True

    def handle_event(self, event: dict[str, Any]) -> bool:
        """Track state from one journal event; relay it if eligible."""
        self.session.apply_event(event)
        self.location.apply_event(event)
        if not (self._armed and self.enabled):
            return False
        if event.get("event") not in JOURNAL_EVENTS:
            return False
        msg = build_journal_message(event, self.session, self.location)
        if msg is None:
            return False
        return self.sender.submit(JOURNAL_SCHEMA_REF, msg, self.session)

    def handle_market(self, market: dict[str, Any]) -> bool:
        """Relay a Market.json snapshot if it is fresh, ours, and new."""
        if not (self._armed and self.enabled):
            return False
        msg = build_commodity_message(market, self.session, self.location)
        if msg is None:
            return False
        # The game rewrites Market.json every time the commodities screen is opened; resend only when the market content actually changed.
        key = _market_key(msg)
        if key == self._last_market_key:
            return False
        self._last_market_key = key
        return self.sender.submit(COMMODITY_SCHEMA_REF, msg, self.session)

    def sync_now(
        self,
        events: Iterable[dict[str, Any]],
        market: dict[str, Any] | None,
        now: float | None = None,
    ) -> tuple[bool, bool]:
        """Force a relay of everything the current session already knows; ``events`` (the journal session in chronological order) is replayed through the trackers first so headers/position/docked market are warm even if the uplink was created mid-session, then the latest position event and Market.json snapshot are relayed with the freshness gate lifted. Returns ``(journal_submitted, market_submitted)``."""
        if not self.enabled:
            return (False, False)

        last_journal: dict[str, Any] | None = None
        for event in events:
            self.session.apply_event(event)
            self.location.apply_event(event)
            if event.get("event") in JOURNAL_EVENTS:
                last_journal = event

        journal_sent = False
        if last_journal is not None:
            msg = build_journal_message(
                last_journal, self.session, self.location, require_fresh=False
            )
            if msg is not None:
                journal_sent = self.sender.submit(
                    JOURNAL_SCHEMA_REF, msg, self.session
                )

        market_sent = False
        if market is not None:
            msg = build_commodity_message(
                market, self.session, self.location, require_fresh=False
            )
            if msg is not None:
                # Note the content so the automatic path doesn't immediately re-send an identical snapshot on its next poll.
                self._last_market_key = _market_key(msg)
                market_sent = self.sender.submit(
                    COMMODITY_SCHEMA_REF, msg, self.session
                )

        return (journal_sent, market_sent)
