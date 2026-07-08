"""Poll-based tailer for Elite Dangerous journals and the Cargo.json snapshot.


    EDSC - Colonization commodities tracker
    Copyright (C) 2026  ThePineappleExpress

    This program is free software: you can redistribute it and/or modify
    it under the terms of the GNU General Public License as published by
    the Free Software Foundation, either version 3 of the License, or
    (at your option) any later version.

    This program is distributed in the hope that it will be useful,
    but WITHOUT ANY WARRANTY; without even the implied warranty of
    MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
    GNU General Public License for more details.

    You should have received a copy of the GNU General Public License
    along with this program.  If not, see <https://www.gnu.org/licenses/>.


"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any, Callable

from . import locator

EventCallback = Callable[[dict[str, Any]], None]
CargoCallback = Callable[[list[dict[str, Any]]], None]


class JournalWatcher:
    """Tails the newest journal file and watches Cargo.json for changes."""

    def __init__(
        self,
        journal_dir: Path,
        on_event: EventCallback,
        on_cargo: CargoCallback | None = None,
        poll_interval: float = 0.5,
    ) -> None:
        self.journal_dir = Path(journal_dir)
        self.on_event = on_event
        self.on_cargo = on_cargo
        self.poll_interval = poll_interval

        self._current_file: Path | None = None
        self._offset = 0
        self._partial = ""  # buffer for a trailing line not yet terminated by \n
        self._cargo_mtime: float | None = None
        self._running = False

    #  history / catch-up 

    def replay_history(
        self,
        files: list[Path] | None = None,
        should_stop: Callable[[], bool] | None = None,
    ) -> None:
        """Read whole journal files in order to reconstruct current state.

        The newest file is read through the tailing path, so the read offset
        (and any trailing half-written line) carries straight over into live
        polling -- events appended between replay and the first poll are not
        lost, and there is no need to call :meth:`prime_latest` afterwards.
        """
        if files is None:
            files = locator.all_journals(self.journal_dir)
        if not files:
            return
        for f in files[:-1]:
            if should_stop is not None and should_stop():
                return
            self._emit_lines(self._read_all(f))
        self._current_file = files[-1]
        self._offset = 0
        self._partial = ""
        self._read_new_bytes(self._current_file)

    def prime_latest(self) -> None:
        """Seek to the end of the newest journal without replaying it.
        """
        latest = locator.latest_journal(self.journal_dir)
        if latest is None:
            self._current_file = None
            self._offset = 0
            return
        self._current_file = latest
        try:
            self._offset = latest.stat().st_size
        except OSError:
            self._offset = 0
        self._partial = ""

    def load_cargo_now(self) -> None:
        """Read Cargo.json immediately (used once at startup)."""
        self._read_cargo(force=True)

    #  live polling

    def poll_once(self) -> None:
        """Do a single pass: pick up new journal lines and cargo changes."""
        self._poll_journal()
        self._read_cargo(force=False)

    def run(self, should_stop: Callable[[], bool] = lambda: False) -> None:
        """Blocking loop; exits when ``should_stop()`` returns True."""
        self._running = True
        try:
            while not should_stop():
                self.poll_once()
                time.sleep(self.poll_interval)
        finally:
            self._running = False

    #  journal internals 

    def _poll_journal(self) -> None:
        latest = locator.latest_journal(self.journal_dir)
        if latest is None:
            return

        # A newer journal file appeared: drain the tail of the old one, then
        # switch. New files start at offset 0.
        if self._current_file is None:
            self._current_file = latest
            self._offset = 0
            self._partial = ""
        elif latest.name != self._current_file.name:
            self._read_new_bytes(self._current_file)  # final flush of old file
            self._current_file = latest
            self._offset = 0
            self._partial = ""

        self._read_new_bytes(self._current_file)

    def _read_new_bytes(self, path: Path) -> None:
        try:
            size = path.stat().st_size
        except OSError:
            return
        # File was truncated/rotated in place: restart from the top.
        if size < self._offset:
            self._offset = 0
            self._partial = ""
        if size == self._offset:
            return
        try:
            with path.open("rb") as fh:
                fh.seek(self._offset)
                chunk = fh.read()
                self._offset = fh.tell()
        except OSError:
            return
        text = self._partial + chunk.decode("utf-8", errors="replace")
        lines = text.split("\n")
        # Last element is an incomplete line (no trailing newline yet); hold it.
        self._partial = lines.pop()
        self._emit_lines(lines)

    def _emit_lines(self, lines: list[str]) -> None:
        for line in lines:
            line = line.strip()
            if not line:
                continue
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(event, dict) and "event" in event:
                self.on_event(event)

    def _read_all(self, path: Path) -> list[str]:
        try:
            return path.read_text(encoding="utf-8", errors="replace").split("\n")
        except OSError:
            return []

    #  cargo internals 

    def _read_cargo(self, force: bool) -> None:
        if self.on_cargo is None:
            return
        cargo_path = self.journal_dir / "Cargo.json"
        try:
            mtime = cargo_path.stat().st_mtime
        except OSError:
            return
        if not force and mtime == self._cargo_mtime:
            return
        self._cargo_mtime = mtime
        try:
            data = json.loads(cargo_path.read_text(encoding="utf-8", errors="replace"))
        except (OSError, json.JSONDecodeError):
            return
        if not isinstance(data, dict):
            return
        # Cargo.json describes whichever vessel you're in; an SRV snapshot must
        # not wipe the tracked ship hold. (Back in the ship, the file is
        # rewritten with Vessel=Ship and picked up on the next poll.)
        if (data.get("Vessel") or "Ship") != "Ship":
            return
        self.on_cargo(data.get("Inventory") or [])
