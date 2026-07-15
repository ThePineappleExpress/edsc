"""EDSC - ED Supply Chain.

A standalone companion overlay for Elite: Dangerous that tracks the commodities
required by your colonisation construction projects, cross-referenced against
what is currently in your ship's and carrier hold and what has already been delivered.

The app reads Elite Dangerous' Journal files (newline-delimited JSON) and the
``Cargo.json`` snapshot, and never talks to the game process directly.

Carrier contents are "best guess" with an option to adjust manually.


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

# Single source of truth for the app version: pyproject.toml reads it from
# here (tool.setuptools.dynamic), and the overlay credit label shows it.
# packaging/aur/PKGBUILD keeps its own pkgver - it must point at the latest
# *tagged* release tarball, so it is bumped when tagging, not here.
__version__ = "0.1.5"

# Steam AppID for Elite Dangerous (used to locate Proton compatdata prefixes).
ELITE_STEAM_APPID = "359320"
