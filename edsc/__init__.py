"""EDSC - ED Supply Chain.

A standalone companion overlay for Elite: Dangerous that tracks the commodities
required by your colonisation construction projects, cross-referenced against
what is currently in your ship's and carrier hold and what has already been delivered.

The app reads Elite Dangerous' Journal files (newline-delimited JSON) and the
``Cargo.json`` snapshot, and never talks to the game process directly.

Carrier contents are "best guess" with an option to adjust manually.
"""

__version__ = "0.1.0"

# Steam AppID for Elite Dangerous (used to locate Proton compatdata prefixes).
ELITE_STEAM_APPID = "359320"
