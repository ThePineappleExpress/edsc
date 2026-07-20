# Test suite review — 2026-07-19

Scope: all 45 files under `tests/` (~11,000 lines, 710 tests), cross-checked
against the `edsc` modules they exercise. Baseline: **710 passed in ~20 s,
87 % line coverage** (12 modules at 100 %), `uv run --extra dev pytest`,
Python 3.14.3.

## Status — addressed same day (2026-07-19)

- **F1 fixed**: `tests/conftest.py` now sets `QT_QPA_PLATFORM=offscreen` before
  any test import and owns the single session-scoped `qapp` fixture; the 17
  per-file copies and 20 per-file env lines are deleted. No ordering hazard
  remains.
- **F3 largely fixed**: new `tests/test_engine.py` (11 tests) covers the
  superseded-sender guards for every slot, the ready/failed handoff, `stop()`
  including the orphan path (with `core.save_state` patched so tests never
  touch the real state dir), and `sync_eddn_now`. `tests/test_core.py` gained
  a save/load round-trip, corrupt-cache tolerance, and two `bootstrap()`
  integration tests — including the pumped-carrier restart scenario end to
  end through core. Coverage: `engine.py` 39 % → 77 % (the remainder is the
  real `QThread`/`_Worker.run` path, deliberately untested), `core.py`
  70 % → 94 %, `watcher.py` 75 % → 81 %. Suite: 726 tests, ~20 s.
- **De-flake**: `test_an_interrupted_collapse_does_not_persist_a_mid_animation_size`
  no longer races the wall-clock animation with `qWait(10)`; it pauses the
  animation group and steps `setCurrentTime()` to a deterministic
  mid-transition point.
- **F2 fixed**: the flash-notice tests monkeypatch `widgets._FLASH_MS` to
  50 ms (read at call time, so the production path in stations_page shrinks
  too). Suite wall time dropped from ~20 s to ~12.5 s.
- **F3 completed to its practical limit**: new `tests/test_carrier_dialog.py`
  (22 % → 100 %) and `tests/test_app.py` — 17 wiring tests on partially built
  `Application` instances (`object.__new__`, the established pattern)
  covering overlay toggling/reveal, docked-state resolution and the journal
  fallback chain, opacity/auto-collapse sync, controller-event dispatch and
  suspension, the EDDN relay consent lifecycle, carrier reset, and the full
  `_shutdown` teardown contract. `__main__` gained `main()` hand-off and
  `_prefer_xcb` platform-pinning tests (46 % → 86 %). `app.py` is 58 %; the
  remainder is `__init__`/`_build_tray`/`run()` — real-QApplication
  construction that headless tests can't meaningfully exercise.
- **F5 fixed**: full suite verified green on Python 3.10 locally
  (753 passed); CI matrix now includes a 3.10 job on ubuntu-22.04.
- **F6 fixed**: `.coverage` gitignored; `test_stations.py` market ids now
  derive from `zlib.crc32` (deterministic under hash randomisation); the
  `edsc_testium` registry key is cleaned up in a `finally`; the EDDN
  close-abort bound tightened from `< 5 s` (equal to the timeout) to `< 2 s`.

## Verdict

The suite is in unusually good shape. Tests are behaviour-driven, use
realistic journal/Spansh/EDDN payloads, place fakes only at true I/O seams
(`urllib.request.urlopen`, the search thread-pool, SDL/joydev device opens),
and regression tests carry docstrings explaining the bug they pin down
("pumped carrier on restart", the replay watermark gate, the same-second
replay edge, SRV cargo snapshots). I found no tautological tests and no test
asserting on mock behaviour instead of code behaviour. The findings below are
mostly structural/hygiene; one is a genuine coverage hole in concurrency
logic.

## What holds up well (spot-checked)

- **Model/state** (`test_model.py`): real journal event shapes, including the
  `$EXT_PANEL_ColonisationShip;` token quirk and carrier capacity =
  `Cargo + FreeSpace`. Replay-gate and watermark edge cases (same-second
  live event, legacy cache without watermark) are all pinned.
- **Spansh contract** (`test_stations.py`, `test_systems.py`): assertions on
  the *request bodies* (filters, page size, category type filters, the 24 h
  `market_updated_at` window) as well as on result ranking — the wire
  contract is tested, not just the post-processing. The carrier-saturated
  page and one-stop-station scenarios encode real API behaviour
  (see memory: Spansh API quirks).
- **EDDN** (`test_eddn.py`): envelopes are validated against the bundled
  official schemas in `tests/schemas/` — genuine contract tests. Sender
  threading is tested with a polling `_wait_for`, not sleeps; retry/backoff,
  queue-full drops, close-aborts-retry, and the replay/arming gate are all
  covered.
- **Isolation**: config-persistence tests monkeypatch `paths.config_dir` to
  `tmp_path`; no test writes to the real XDG dirs (verified: nothing calls
  `Engine.stop()` / `core.save_state` / `core.load_cached_state`). Network
  is always faked. `test_main.py` carefully restores the `EDSC_DEV`/
  `EDSC_TRACE` env switches.
- **Platform code**: Linux joydev tests are `skipif`-gated and mock
  `fcntl.ioctl` / device opens; nothing touches real `/dev/input`.

## Findings

### F1 — `QT_QPA_PLATFORM=offscreen` relies on collection order (high, cheap fix)

There is no `tests/conftest.py`. Each Qt test file individually does
`os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")` before importing
PySide6, and 17 files hand-roll an identical module-scoped `qapp` fixture.
Two files create a `QApplication` but never set the variable:

- `tests/test_flow_layout.py`
- `tests/test_colonize_filters.py`

Full-suite runs only work because pytest imports `test_about_easter_egg.py`
(alphabetically first) during collection, which sets the variable for
everyone. Consequences today: a solo run (`pytest tests/test_flow_layout.py`)
aborts on a truly headless box and attaches to the live compositor on a
desktop; adding or renaming a Qt test file that sorts before `about` would
break headless CI in a confusing way.

**Fix**: add `tests/conftest.py` that sets the env var (before any Qt
import) and hosts a single session-scoped `qapp` fixture; delete the 17
copies.

### F2 — Four tests burn over half the suite's runtime on real 2.5 s timers (medium)

`edsc/gui/widgets.py` has `_FLASH_MS = 2500`, and the flash-notice tests wait
it out in real time (`QTest.qWait(_FLASH_MS + 200)`):

| Test | Time |
|---|---|
| `test_widgets.py::test_a_notice_replaces_the_text_and_then_gives_it_back` | 2.70 s |
| `test_widgets.py::test_a_notice_never_clobbers_text_that_replaced_it` | 2.70 s |
| `test_widgets.py::test_the_restore_is_dropped_when_the_label_dies_first` | 2.70 s |
| `test_stations_page.py::test_the_copy_notice_gives_the_status_line_back` | 2.65 s |

That is ~10.8 s of the ~19.7 s wall time. **Fix**: make the flash duration
injectable (parameter with default, or patch the module attribute in tests)
and wait ~50 ms. Same coverage, suite drops to ~9 s, and less exposure to
loaded-CI timing.

### F3 — Engine/App orchestration is the one real coverage hole (medium)

| Module | Coverage | What is untested |
|---|---|---|
| `edsc/engine.py` | 39 % | The whole `_Worker`/`QThread` lifecycle: `start()`, the `ready` state-handoff, `stop()` with its 2 s grace + orphan list, `sync_eddn_now()` |
| `edsc/gui/app.py` | 48 % | Application wiring, shutdown, config-save paths |
| `edsc/core.py` | 70 % | `bootstrap()`, `load_cached_state()`/`save_state()` round-trip |
| `edsc/gui/carrier_dialog.py` | 22 % | Nearly the whole dialog |
| `edsc/journal/watcher.py` | 75 % | The blocking `run()` loop, `OSError` branches, file-truncation recovery |
| `edsc/__main__.py` | 46 % | `main()` itself (only `_apply_cli` is tested) |

The most valuable gap is the engine's *superseded-worker guards*
(`if self.sender() is not self._worker: return` in every slot). They encode
the correctness rule that a restarted engine (journal-dir change) must ignore
queued signals from the old thread — subtle concurrency logic with zero
tests. The slots are already directly callable (`test_thargoids.py` calls
`engine._on_event(...)`), so guard tests don't need real threads. A
`core.bootstrap()` + save/load round-trip against `tmp_path` (monkeypatching
`paths.state_dir`) would cover most of the rest.

### F4 — Platform-specific modules are near-untested, acceptably (low)

`hotkeys.py` 15 %, `foreground.py` 42 %, `clickthrough.py` 55 %,
`topmost.py` 62 %, `glitch_overlay.py` 64 %. These are X11/Win32 calls and
paint-time animation code that headless CI cannot meaningfully exercise.
Reasonable to accept; recording it here so the number isn't re-litigated.

### F5 — CI tests only Python 3.14, pyproject claims ≥3.10 (low)

`requires-python = ">=3.10"` but the matrix pins 3.14 on both OSes. The
claimed floor is unverified — either add one 3.10 job or raise the floor.

### F6 — Nits (low)

- `.coverage` sits untracked at the repo root and is missing from
  `.gitignore` (which does list `.pytest_cache/` and `.ruff_cache/`).
- `test_stations.py::_station()` derives `market_id` from
  `abs(hash(name)) % 1_000_000` — differs per run under hash randomisation
  and can in principle collide. Nothing asserts on the ids today; a counter
  would be deterministic.
- `test_model.py::test_display_names_survive_cache_round_trip` leaves the
  fake `edsc_testium` key registered in the module-global
  `commodities._DISPLAY_REGISTRY` after the test. Harmless (unique fake key),
  but worth a `monkeypatch`-style cleanup if the registry ever grows
  behaviour.
- `systems._agent_cache`/`_verify_cache` are module globals cleared by an
  autouse fixture *local to `test_systems.py`*. If any other test file ever
  calls the systems search paths, that cache leaks across tests — fine today,
  a trap later.
- `test_eddn.py::test_sender_close_aborts_pending_retry_quickly` asserts
  `elapsed < 5.0` with `close(timeout=5.0)` — passes because close aborts in
  milliseconds, but the bound equals the timeout, so a pathological CI stall
  reads as the wrong failure. A tighter bound (e.g. `< 2.0`) would say what
  it means.

## Remaining accepted gaps

All findings are addressed. What stays uncovered is accepted by design:
platform-specific modules (F4), `Application.__init__`/`_build_tray`/`run()`
and `engine._Worker.run()` (real QApplication/QThread paths a headless suite
can't meaningfully exercise), and the watcher's blocking loop.
