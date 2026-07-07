"""Entry point: ``python -m edsc`` (or the ``edsc`` console script)."""

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
