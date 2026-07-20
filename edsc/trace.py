"""Console tracing for the Spansh searches, silent unless switched on; set ``EDSC_TRACE=1`` (or the broader ``EDSC_DEV=1``) to print every request, response summary and pipeline stage to stderr with timings. Nothing here runs in a normal session beyond an env lookup, so call sites trace freely without guarding each one. Requests are echoed in full (they're small, and a filter matching nothing is visible right there); responses as a shape summary not verbatim (a systems page is half a MB of JSON that would bury the line you're reading the trace for)."""

# SPDX-License-Identifier: GPL-3.0-or-later

from __future__ import annotations

import json
import os
import sys
import time
from collections.abc import Iterator
from contextlib import contextmanager

_TRUTHY = {"1", "true", "yes", "on"}
_MAX_DUMP = 2000  # chars of JSON echoed per line before truncation


def enabled() -> bool:
    """True when either trace switch is set to a truthy value."""
    return any(
        os.environ.get(var, "").strip().lower() in _TRUTHY
        for var in ("EDSC_TRACE", "EDSC_DEV")
    )


def log(message: str) -> None:
    """Print one timestamped trace line to stderr."""
    if not enabled():
        return
    print(f"[edsc {time.strftime('%H:%M:%S')}] {message}", file=sys.stderr, flush=True)


def dump(label: str, payload: object) -> None:
    """Log ``payload`` as compact JSON, truncated to keep the line readable."""
    if not enabled():
        return
    try:
        text = json.dumps(payload, default=str, sort_keys=True)
    except (TypeError, ValueError):
        text = repr(payload)
    if len(text) > _MAX_DUMP:
        text = f"{text[:_MAX_DUMP]}… (+{len(text) - _MAX_DUMP} more chars)"
    log(f"{label} {text}")


class Note:
    """Detail a :func:`timed` block attaches to its own summary line; the block usually only knows what it did once done (rows back, rows surviving), so it reports that here rather than in the label."""

    def __init__(self) -> None:
        self.text = ""

    def say(self, text: str) -> None:
        self.text = text


@contextmanager
def timed(label: str) -> Iterator[Note]:
    """Time a block and log ``label``, its elapsed ms and the note it set; the line is emitted even when the block raises, so a failed lookup still shows how long it took to fail."""
    note = Note()
    start = time.perf_counter()
    try:
        yield note
    finally:
        ms = (time.perf_counter() - start) * 1000.0
        detail = f" · {note.text}" if note.text else ""
        log(f"{label} · {ms:.0f} ms{detail}")
