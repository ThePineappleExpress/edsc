"""Command-line and module entry point."""

# SPDX-License-Identifier: GPL-3.0-or-later

from __future__ import annotations

import argparse
import os
import sys


def _apply_cli(argv: list[str]) -> None:
    """Turn EDSC's own flags into the environment switches they stand for; ``--dev`` is exported rather than threaded through ``run()`` because the switch is read lazily and far apart (console tracing and the controller tester each look it up with no shared object to hang a flag off). Qt's own arguments (``-platform`` and friends) are left in ``sys.argv`` for ``QApplication``."""
    parser = argparse.ArgumentParser(
        prog="edsc",
        description="Elite Dangerous Supply Chain overlay.",
    )
    parser.add_argument(
        "--dev",
        action="store_true",
        help=(
            "Enable development mode: console tracing for searches, plus the "
            "live controller tester in Settings. Same as EDSC_DEV=1."
        ),
    )
    args, _ = parser.parse_known_args(argv)
    if args.dev:
        os.environ["EDSC_DEV"] = "1"


def _prefer_xcb() -> None:
    """Pin the Qt platform to xcb on Linux X11 sessions before Qt loads; must run before ``QApplication`` is constructed, and is skipped on Windows, when the platform is already chosen (e.g. tests set ``offscreen``), or when no X display is present."""
    if sys.platform == "win32":
        return
    if os.environ.get("QT_QPA_PLATFORM"):
        return
    if os.environ.get("DISPLAY"):
        os.environ["QT_QPA_PLATFORM"] = "xcb"


def main(argv: list[str] | None = None) -> int:
    _apply_cli(sys.argv[1:] if argv is None else argv)
    _prefer_xcb()
    # Works as `python -m edsc` / the `edsc` console script (package context present) and when run directly as `python edsc/__main__.py` (no parent package) -- in the latter case, put the project root on the path and import absolutely.
    try:
        from .gui.app import run
    except ImportError:
        sys.path.insert(
            0, os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        )
        from edsc.gui.app import run

    return run()


if __name__ == "__main__":
    sys.exit(main())
