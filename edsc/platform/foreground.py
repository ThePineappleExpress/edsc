"""Detect the active window through X11 or Win32."""

# SPDX-License-Identifier: GPL-3.0-or-later

from __future__ import annotations

import os
import sys
from contextlib import suppress
from dataclasses import dataclass


@dataclass
class ForegroundInfo:
    wm_class: str = ""
    title: str = ""

    def matches(self, needles: list[str]) -> bool:
        haystack = f"{self.wm_class} {self.title}".lower()
        return any(n and n.lower() in haystack for n in needles)


class ForegroundDetector:
    """Base / null detector. ``available`` is False when detection can't work."""

    available = False

    def active(self) -> ForegroundInfo | None:
        return None

    def close(self) -> None:
        pass


class X11ForegroundDetector(ForegroundDetector):
    def __init__(self) -> None:
        from Xlib import X, display

        self._X = X
        self._display = display.Display()
        self._root = self._display.screen().root
        self._net_active = self._display.intern_atom("_NET_ACTIVE_WINDOW")
        self._net_name = self._display.intern_atom("_NET_WM_NAME")
        self.available = True

    def active(self) -> ForegroundInfo | None:
        try:
            prop = self._root.get_full_property(self._net_active, self._X.AnyPropertyType)
            if not prop or not prop.value:
                return None
            wid = int(prop.value[0])
            if not wid:
                return None
            win = self._display.create_resource_object("window", wid)
            return ForegroundInfo(self._wm_class(win), self._title(win))
        except Exception:
            # Window vanished mid-query (BadWindow), or transient X error.
            return None

    def _wm_class(self, win) -> str:
        try:
            wc = win.get_wm_class()
            return " ".join(p for p in wc if p) if wc else ""
        except Exception:
            return ""

    def _title(self, win) -> str:
        try:
            prop = win.get_full_property(self._net_name, 0)
            if prop and prop.value:
                value = prop.value
                if isinstance(value, bytes):
                    return value.decode("utf-8", "replace")
                return str(value)
            return win.get_wm_name() or ""
        except Exception:
            return ""

    def close(self) -> None:
        with suppress(Exception):
            self._display.close()


class WindowsForegroundDetector(ForegroundDetector):
    def __init__(self) -> None:
        import ctypes

        self._ctypes = ctypes
        self._user32 = ctypes.windll.user32
        self.available = True

    def active(self) -> ForegroundInfo | None:
        ctypes = self._ctypes
        user32 = self._user32
        hwnd = user32.GetForegroundWindow()
        if not hwnd:
            return None
        length = user32.GetWindowTextLengthW(hwnd)
        title_buf = ctypes.create_unicode_buffer(length + 1)
        user32.GetWindowTextW(hwnd, title_buf, length + 1)
        class_buf = ctypes.create_unicode_buffer(256)
        user32.GetClassNameW(hwnd, class_buf, 256)
        return ForegroundInfo(class_buf.value, title_buf.value)


def make_detector() -> ForegroundDetector:
    """Best available detector for this platform/session, or a null detector."""
    if sys.platform == "win32":
        try:
            return WindowsForegroundDetector()
        except Exception:
            return ForegroundDetector()
    if os.environ.get("DISPLAY"):
        try:
            return X11ForegroundDetector()
        except Exception:
            return ForegroundDetector()
    return ForegroundDetector()
