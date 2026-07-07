"""Make a top-level Qt window transparent to mouse input (click-through), so
clicks land on the game behind it.
"""

from __future__ import annotations

import os
import sys

from PySide6.QtCore import Qt

_x11_display = None


def set_click_through(window, enabled: bool) -> bool:
    """Toggle click-through on ``window``. Returns True if a *native* pass-through
    was applied (not just the Qt attribute fallback)."""
    window.setAttribute(Qt.WA_TransparentForMouseEvents, enabled)
    if sys.platform == "win32":
        return _windows_set(window, enabled)
    if os.environ.get("DISPLAY"):
        return _x11_set(window, enabled)
    return False


def _x11_conn():
    global _x11_display
    if _x11_display is None:
        from Xlib import display

        _x11_display = display.Display()
    return _x11_display


def _x11_set(window, enabled: bool) -> bool:
    try:
        from Xlib import X
        from Xlib.ext import shape

        wid = int(window.winId())
        if not wid:
            return False
        conn = _x11_conn()
        xwin = conn.create_resource_object("window", wid)
        if enabled:
            # Empty input region -> pointer events fall through to windows below.
            xwin.shape_rectangles(shape.SO.Set, shape.SK.Input, X.Unsorted, 0, 0, [])
        else:
            # Reset input region to the full window (X.NONE bitmap = whole window).
            xwin.shape_mask(shape.SO.Set, shape.SK.Input, 0, 0, X.NONE)
        conn.sync()
        return True
    except Exception:
        return False


def _windows_set(window, enabled: bool) -> bool:
    try:
        import ctypes

        GWL_EXSTYLE = -20
        WS_EX_LAYERED = 0x00080000
        WS_EX_TRANSPARENT = 0x00000020

        hwnd = int(window.winId())
        user32 = ctypes.windll.user32
        style = user32.GetWindowLongW(hwnd, GWL_EXSTYLE)
        if enabled:
            style |= WS_EX_LAYERED | WS_EX_TRANSPARENT
        else:
            style &= ~WS_EX_TRANSPARENT
        user32.SetWindowLongW(hwnd, GWL_EXSTYLE, style)
        return True
    except Exception:
        return False
