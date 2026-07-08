# ED Supply Chain (EDSC)

A standalone companion **overlay** for *Elite: Dangerous* that tracks the
commodities your colonisation construction projects need - cross-referenced
against what's currently in your ship's/carrier's hold and what's already delivered.

For every construction site you've docked at, EDSC shows each commodity with:

| Column | Meaning |
| ------ | ------- |
| **Need**    | Total units the construction requires |
| **Hold**    | How many you're carrying right now (from `Cargo.json`) |
| **Carrier** | How many are staged on your fleet carrier (tracked - see below) |
| **Done**   | How many have already been delivered to the depot |
| **Short**   | How many you still need to acquire (`Need − Deliv − Hold`) |

Fully-delivered commodities are dimmed and sink to the bottom; anything you're
already carrying enough of to complete is highlighted.

## Multiple constructions & tabs

Each construction you've docked at gets its own **tab**, plus an **All** tab that
combines every construction's outstanding needs into one list (so you can see the
total of each commodity to haul across all your projects at once). Switch tabs
with **`Ctrl+Shift+←` / `Ctrl+Shift+→`** - this works even while you're in the
game (via a global hotkey), as well as when the overlay is focused. The global
hotkey is only grabbed **while the game window is focused**, so the combos keep
working normally in your other applications.

Right-click a project's tab to **remove** it (useful once a construction is
finished or abandoned) - docking at the site again brings it back.

## Nearest stations (⚑ Stations tab)

The trailing **⚑ Stations** tab answers *"where do I buy all this?"*: it queries
the community [Spansh](https://spansh.co.uk) station search for the nearest
**large-pad** stations selling the commodities you're still short of (aggregated
across all your active constructions, minus what's already in your hold and on
your carrier), ranked by how much of your list each station covers, then by
distance. Hover a row to see exactly which needed commodities a station stocks
and how fresh its market data is.

- Your current system is the search origin, tracked from jump/location events -
  you don't need to dock first.
- A station only counts as stocking a commodity when its supply covers your
  remaining shortfall (capped at a pragmatic 100 t floor for large shortfalls),
  so a market listing 2 t against a 5,000 t need doesn't light up.
- **Include planets** toggles surface stations; **↻ Search** forces a refresh.
  The search also re-runs automatically when your system or the set of needed
  commodities changes.
- Market data is community-sourced (EDDN) and can be stale - check the tooltip's
  *"Market data from"* line before a long haul.

This is EDSC's **only** network feature; everything else works entirely from
local journal files. 

## Fleet carrier column

Elite Dangerous **does not** expose an itemised fleet-carrier inventory to third
parties without the Frontier Companion API (which needs a Frontier-issued OAuth
client ID that isn't available to personal apps). The journal only provides the
carrier's *total* tonnage plus ship↔carrier transfer *deltas*.

So EDSC tracks the carrier column from `CargoTransfer` events (persisted across
sessions) and shows a summary like *"FC VZV-45V · tracking 6,469 t of 11,586 t"*.
When the tracked figure is below the true total, the difference is cargo that was
already on the carrier before EDSC ever saw it. To fix that, click **`FC…`** and
enter the real amounts for the commodities in view - transfers you make afterward
keep them updated. **Reset fleet-carrier cargo** in the tray menu clears tracking.

## How it works

EDSC never touches the game process. It reads Elite Dangerous' **Journal** files
(newline-delimited JSON) and the `Cargo.json` snapshot that the game writes as
you play:

- `ColonisationConstructionDepot` (emitted when you dock at a construction site)
  provides each commodity's required/provided amounts.
- `ColonisationContribution` updates the overlay the instant you hand cargo over.
- `Cargo.json` is the live contents of your hold.
- `Docked` events name each project (station + system).
- `FSDJump` / `Location` / `CarrierJump` track your current system - the
  reference point for the nearest-stations search.
- `CargoTransfer` / `CarrierStats` feed the fleet-carrier tracking above.

On startup it replays your journal history so previously-visited projects
reappear, then live-tails the newest journal for updates.

## Requirements

- Python 3.10+
- [PySide6](https://pypi.org/project/PySide6/) (installed automatically below)

## Install & run

```bash
python -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -e .
edsc                                # or: python -m edsc
```

The overlay appears as a frameless, translucent, always-on-top panel. Drag it by
its header. It also lives in the system tray - click the tray icon to show/hide,
or right-click for Settings / Quit.

The overlay's **height auto-fits the number of commodities** in the list (long
lists scroll once they'd run off-screen); drag the corner grip to set the width.

### Focus-aware click-through

By default the overlay **ignores the mouse while Elite Dangerous is focused**, so
clicks pass straight through to the game - and becomes **movable again the moment
the game isn't focused** (e.g. when you alt-tab to it). Drag it by its header when
it's interactive. Toggle this behaviour with the ▨ button, or turn it off to keep
the overlay always interactive.

The game window is recognised by matching substrings (default
`steam_app_359320`, `elite - dangerous (client)`, `elitedangerous64`) against
the focused window's class/title - editable in **Settings** if your setup
reports a different name. The defaults are deliberately specific so that e.g.
a browser tab with "Elite Dangerous" in its title doesn't trigger
click-through.

### Overlay controls (header buttons)

- ▲ **Pin** - keep the overlay above other windows
- ▨ **Auto click-through** - pass the mouse through while the game is focused
- ⚙ **Settings** - journal folder, opacity, font, auto-height, game matchers
- **-** **Hide** - hide to the tray

### Display mode (important for always-on-top)

EDSC is a normal external window that never touches the game process, so it can
only appear above Elite Dangerous when the game plays by the window manager's
stacking rules. **Exclusive (true) fullscreen deliberately bypasses those rules**
- the compositor unredirects the game so it owns the whole screen, and *no*
external overlay (on any OS) can draw over it. This is a limitation of exclusive
fullscreen itself, not of EDSC.

To keep the overlay visible, do **one** of:

- **Set ED to *Borderless* / *Windowed (Fullscreen)*** - *Options → Graphics →
  Display → Borderless*. It looks identical to fullscreen but the game is a
  normal top-level window, so the overlay stays on top. **Recommended.**
- **(Linux/KDE only) stop the compositor unredirecting fullscreen** - *System
  Settings → Display & Monitor → Compositor → uncheck "Allow applications to
  block compositing"*. Then the overlay shows even over the game's fullscreen
  window (at a small performance cost). Other desktops may not offer this.

Plain windowed mode is **not** required - Borderless is enough.

#### Still hidden in Borderless on KDE Plasma?

On KWin (KDE), a **focused** window that reports a full-screen state is placed in
a stacking layer *above* ordinary "keep above" windows - and Elite Dangerous
under Proton/DXVK keeps asserting that full-screen state even in **Borderless**.
So `WindowStaysOnTopHint` alone can't win, and EDSC can't fix it purely from
outside the game. Add a one-time **KWin Window Rule** so the game stops taking
that top layer (its Borderless appearance is unchanged):

1. Focus Elite Dangerous, then *right-click its title area →* **More Actions →
   Configure Special Application Settings…** - or *System Settings → Window
   Management → Window Rules → Add New… → Detect Window Properties* and click the
   game window (this fills in its window class for you; ED under Proton usually
   reports something like `steam_app_359320` / `elitedangerous64.exe`).
2. Add property **Fullscreen** → set to **Force** → **No**.
3. *(Optional, belt-and-braces)* add a second rule, this time detecting the
   EDSC overlay window (class `edsc`), with **Keep above other windows** →
   **Force** → **Yes**.
4. Apply, then alt-tab in/out of the game once.

With the game no longer in the full-screen layer, EDSC's always-on-top (which it
also re-asserts whenever the game grabs focus) keeps it visible.

## Journal folder detection

EDSC auto-detects the journal folder per platform:

- **Linux (Steam Proton)** - probes Flatpak, native, and Snap Steam layouts for
  Elite Dangerous (AppID `359320`), including extra Steam library folders, e.g.
  `~/.var/app/com.valvesoftware.Steam/.local/share/Steam/steamapps/compatdata/359320/pfx/drive_c/users/steamuser/Saved Games/Frontier Developments/Elite Dangerous`
- **Windows** - `%USERPROFILE%\Saved Games\Frontier Developments\Elite Dangerous`
- **macOS** - `~/Library/Application Support/Frontier Developments/Elite Dangerous`

Override it in **Settings**, or with the `EDSC_JOURNAL_DIR` environment variable.

### Linux / Wayland note

Wayland gives ordinary apps no portable way to read which window is focused, do
true click-through, or stay reliably on top. Elite Dangerous runs under Proton as
an **XWayland (X11)** app, so on Linux EDSC runs itself on XWayland too (it sets
`QT_QPA_PLATFORM=xcb`) - that puts the overlay and the game on the same X server,
which is what makes focus detection and click-through work. Set
`QT_QPA_PLATFORM` yourself to override. On KDE/GNOME Wayland this is transparent.

## Development

```bash
pip install -e ".[dev]"
pytest                              # headless unit tests (no display needed)
```

### Layout

```
edsc/
  commodities.py     canonical name normalisation ($aluminium_name; ↔ aluminium)
  config.py          persisted user settings
  paths.py           per-OS config/state directories
  model.py           Project / Commodity / AppState merge logic (pure, tested)
  core.py            journal-dir resolution, state bootstrap, persistence
  engine.py          Qt engine: background journal thread → GUI signals
  stations.py        Spansh nearest-station search client (the ⚑ Stations tab)
  journal/
    locator.py       cross-platform journal-folder discovery
    watcher.py       poll-based tailer (rollover-safe) + Cargo.json watch
  platform/
    foreground.py    which window is focused (X11/XWayland + Windows backends)
    clickthrough.py  true mouse pass-through (X11 input-shape / Win32 ex-style)
    topmost.py       re-assert keep-above when the game grabs focus (X11/Win32)
    hotkeys.py       global X11 hotkeys (XGrabKey) pumped via QSocketNotifier
  gui/
    overlay.py       the translucent always-on-top overlay window
    table_model.py   commodity + station table models with HUD colour coding
    carrier_dialog.py  manual correction of tracked fleet-carrier amounts
    settings_dialog.py
    theme.py         Elite Dangerous HUD palette + stylesheet
    app.py           QApplication + tray bootstrap
tests/               headless tests: model, stations, watcher, locator, core,
                     location tracking, name normalisation (no display, no network)
```

The domain core (`model.py`, `journal/`, `core.py`, `stations.py`) is Qt-free
and fully unit-tested; the GUI is a thin rendering layer on top.

Bugs? Suggestions? E-mail dev@thepineapple.express
