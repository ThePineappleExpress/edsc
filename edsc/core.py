"""Non-GUI glue: journal-dir resolution, state bootstrap, and persistence.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from . import paths
from .config import Config
from .journal import locator
from .journal.watcher import JournalWatcher
from .model import AppState

STATE_FILENAME = "state.json"

# Safety margin when comparing journal mtimes (filesystem clock) against the
# persisted event watermark (game-written UTC): over-keeping a file is cheap,
# over-skipping loses events.
_REPLAY_MTIME_MARGIN_S = 3600.0


def resolve_journal_dir(config: Config) -> Path | None:
    """Locate the journal directory, honouring the config override first."""
    return locator.find_journal_dir(config.journal_dir or None)


def state_file() -> Path:
    return paths.state_dir() / STATE_FILENAME


def load_cached_state() -> AppState:
    """Load the last-persisted state, or an empty one if none/invalid."""
    try:
        data = json.loads(state_file().read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return AppState()
    return AppState.from_dict(data)


def save_state(state: AppState) -> None:
    """Persist state atomically so projects survive between game sessions."""
    directory = paths.ensure_dir(paths.state_dir())
    tmp = directory / (STATE_FILENAME + ".tmp")
    tmp.write_text(json.dumps(state.to_dict(), indent=2), encoding="utf-8")
    tmp.replace(directory / STATE_FILENAME)


def build_watcher(journal_dir: Path, state: AppState) -> JournalWatcher:
    """Create a watcher whose callbacks apply directly to ``state``."""
    return JournalWatcher(
        journal_dir,
        on_event=state.apply_event,
        on_cargo=state.set_cargo,
    )


def _watermark_epoch(watermark: str) -> float | None:
    """The persisted event watermark as a Unix timestamp, or None if unusable."""
    if not watermark:
        return None
    try:
        dt = datetime.strptime(watermark, "%Y-%m-%dT%H:%M:%SZ")
    except ValueError:
        return None
    return dt.replace(tzinfo=timezone.utc).timestamp()


def journals_to_replay(journal_dir: Path, watermark: str) -> list[Path]:
    """Journal files still worth replaying on top of a cached state.

    Everything in a file whose last write predates the watermark (minus a
    safety margin) was already folded into the persisted state, so it can be
    skipped -- this keeps startup fast even with years of journal history.
    Without a usable watermark every file is replayed.
    """
    files = locator.all_journals(journal_dir)
    cutoff = _watermark_epoch(watermark)
    if cutoff is None:
        return files
    cutoff -= _REPLAY_MTIME_MARGIN_S
    kept: list[Path] = []
    for f in files:
        try:
            if f.stat().st_mtime >= cutoff:
                kept.append(f)
        except OSError:
            kept.append(f)  # unreadable stat: keep, replay decides
    return kept


def bootstrap(
    journal_dir: Path,
    state: AppState | None = None,
    should_stop: Callable[[], bool] | None = None,
) -> tuple[AppState, JournalWatcher]:
    """Reconstruct current state from journal history and prime live tailing.

    Replays journal files not already covered by the cached state's watermark
    (so previously-visited projects reappear), loads the current cargo, and
    leaves the watcher positioned at the end of the replayed history so a
    subsequent ``run``/``poll_once`` only sees genuinely new events.
    """
    if state is None:
        state = AppState()
    watcher = build_watcher(journal_dir, state)
    files = journals_to_replay(journal_dir, state.last_event_time)
    watcher.replay_history(files, should_stop=should_stop)
    # History is fully replayed: release the carrier replay gate so subsequent
    # live CargoTransfer events are applied normally.
    state.finish_replay()
    watcher.load_cargo_now()
    # Replay positions the tail on the newest file it read; if every file was
    # skipped as stale (or the dir is empty), seek to the end explicitly so
    # old events are not re-read by the first poll.
    if not files or files[-1] != locator.latest_journal(journal_dir):
        watcher.prime_latest()
    return state, watcher
