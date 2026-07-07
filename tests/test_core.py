import os
from datetime import datetime, timezone

from edsc import core


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
    """The mtime comparison has a safety margin: files written shortly before
    the watermark are still replayed rather than risking lost events."""
    near = _journal(tmp_path, "Journal.2026-07-04T000000.01.log", "2026-07-04T23:45:00Z")
    files = core.journals_to_replay(tmp_path, "2026-07-05T00:00:00Z")
    assert files == [near]
