"""Entry point: ``python -m edsc`` (or the ``edsc`` console script).


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

import sys


def main() -> int:
    # Works both as `python -m edsc` / the `edsc` console script (package context
    # present) and when this file is run directly as `python edsc/__main__.py`
    # (no parent package) -- in the latter case, put the project root on the path
    # and import absolutely.
    try:
        from .gui.app import run
    except ImportError:
        import os

        sys.path.insert(
            0, os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        )
        from edsc.gui.app import run

    return run()


if __name__ == "__main__":
    sys.exit(main())
