"""Re-assert a window's "keep above" stacking more forcefully than Qt's
``WindowStaysOnTopHint`` alone.


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

import os
import sys

_x11_display = None


def assert_above(window) -> bool:
    """(Re)assert keep-above + raise ``window``. Returns True on a native apply."""
    if sys.platform == "win32":
        return _windows_topmost(window)
    if os.environ.get("DISPLAY"):
        return _x11_above(window)
    return False


def _x11_conn():
    global _x11_display
    if _x11_display is None:
        from Xlib import display

        _x11_display = display.Display()
    return _x11_display


def _x11_above(window) -> bool:
    try:
        from Xlib import X, protocol

        wid = int(window.winId())
        if not wid:
            return False
        conn = _x11_conn()
        root = conn.screen().root
        xwin = conn.create_resource_object("window", wid)

        net_state = conn.intern_atom("_NET_WM_STATE")
        net_above = conn.intern_atom("_NET_WM_STATE_ABOVE")

        # _NET_WM_STATE client message: action=1 (ADD), property=_NET_WM_STATE_ABOVE,
        # source indication=1 (normal application). Sent to the root per EWMH.
        data = [1, net_above, 0, 1, 0]
        event = protocol.event.ClientMessage(
            window=xwin, client_type=net_state, data=(32, data)
        )
        mask = X.SubstructureRedirectMask | X.SubstructureNotifyMask
        root.send_event(event, event_mask=mask)

        # Raise within the window's own stacking layer (won't beat an active
        # full-screen window, but keeps us above normal/borderless windows).
        xwin.configure(stack_mode=X.Above)
        conn.flush()
        return True
    except Exception:
        return False


def _windows_topmost(window) -> bool:
    try:
        import ctypes

        HWND_TOPMOST = -1
        SWP_NOMOVE = 0x0002
        SWP_NOSIZE = 0x0001
        SWP_NOACTIVATE = 0x0010

        hwnd = int(window.winId())
        ctypes.windll.user32.SetWindowPos(
            hwnd, HWND_TOPMOST, 0, 0, 0, 0,
            SWP_NOMOVE | SWP_NOSIZE | SWP_NOACTIVATE,
        )
        return True
    except Exception:
        return False
