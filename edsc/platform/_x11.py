"""Shared lazy X11 display connection for native window helpers."""

# SPDX-License-Identifier: GPL-3.0-or-later

from __future__ import annotations

_display = None


def connection():
    global _display
    if _display is None:
        from Xlib import display

        _display = display.Display()
    return _display
