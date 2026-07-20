"""Register system-wide hotkeys on X11 and XWayland."""

# SPDX-License-Identifier: GPL-3.0-or-later

from __future__ import annotations

import os
from collections.abc import Callable
from contextlib import suppress

from PySide6.QtCore import QObject, QSocketNotifier

_MOD_MAP = {
    "CTRL": "ControlMask",
    "CONTROL": "ControlMask",
    "SHIFT": "ShiftMask",
    "ALT": "Mod1Mask",
    "SUPER": "Mod4Mask",
    "META": "Mod4Mask",
}


class GlobalHotkeys(QObject):
    """Registers global key combos and calls back on the GUI thread; bindings are only *grabbed* while :meth:`set_active` has switched them on (the app activates them while the game window is focused), since grabbing them permanently would steal the combos from every other application."""

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self.available = False
        self._display = None
        self._root = None
        self._notifier: QSocketNotifier | None = None
        self._bindings: dict[tuple[int, int], Callable[[], None]] = {}
        self._grabbed: list[tuple[int, int]] = []
        self._mod_mask = 0
        self._active = False

        if os.environ.get("DISPLAY"):
            self._open()

    def _open(self) -> None:
        try:
            from Xlib import X, display

            self._X = X
            self._display = display.Display()
            self._root = self._display.screen().root
            self._mod_mask = X.ControlMask | X.ShiftMask | X.Mod1Mask | X.Mod4Mask
            # Combinations of "lock" modifiers to grab alongside, so the hotkey still fires with CapsLock/NumLock on.
            self._lock_masks = [
                0,
                X.LockMask,
                X.Mod2Mask,
                X.LockMask | X.Mod2Mask,
            ]
            fd = self._display.fileno()
            self._notifier = QSocketNotifier(fd, QSocketNotifier.Read, self)
            self._notifier.activated.connect(self._on_ready)
            self.available = True
        except Exception:
            self.available = False

    def bind(self, sequence: str, callback: Callable[[], None]) -> bool:
        """Register ``sequence`` (e.g. 'Ctrl+Shift+Left'). Returns True on success; the combo is grabbed lazily, once :meth:`set_active` switches to True."""
        if not self.available:
            return False
        parsed = self._parse(sequence)
        if parsed is None:
            return False
        modifiers, keycode = parsed
        if not keycode:
            return False
        self._bindings[(keycode, modifiers)] = callback
        return not self._active or self._grab(keycode, modifiers)

    def set_active(self, active: bool) -> None:
        """Grab the bound combos (True) or release them (False)."""
        active = bool(active)
        if not self.available or active == self._active:
            return
        self._active = active
        if active:
            for keycode, modifiers in self._bindings:
                self._grab(keycode, modifiers)
        else:
            self._ungrab_all()

    #  internals 

    def _parse(self, sequence: str):
        from Xlib import XK

        modifiers = 0
        keysym = None
        for part in sequence.replace(" ", "").split("+"):
            token = part.upper()
            if token in _MOD_MAP:
                modifiers |= getattr(self._X, _MOD_MAP[token])
            else:
                keysym = XK.string_to_keysym(part)
        if not keysym:
            return None
        keycode = self._display.keysym_to_keycode(keysym)
        return modifiers, keycode

    def _grab(self, keycode: int, modifiers: int) -> bool:
        from Xlib import X, error

        ok = True
        for lock in self._lock_masks:
            catch = error.CatchError(error.BadAccess)
            self._root.grab_key(
                keycode,
                modifiers | lock,
                True,
                X.GrabModeAsync,
                X.GrabModeAsync,
                onerror=catch,
            )
            self._display.sync()
            if catch.get_error():
                ok = False
        if ok:
            self._grabbed.append((keycode, modifiers))
        return ok

    def _on_ready(self, *_args) -> None:
        d = self._display
        if d is None:
            return
        try:
            pending = d.pending_events()
            while pending:
                ev = d.next_event()
                if ev.type == self._X.KeyPress:
                    self._dispatch(ev)
                pending = d.pending_events()
        except Exception:
            pass

    def _dispatch(self, ev) -> None:
        modifiers = ev.state & self._mod_mask
        callback = self._bindings.get((ev.detail, modifiers))
        if callback is not None:
            callback()

    def _ungrab_all(self) -> None:
        with suppress(Exception):
            for keycode, modifiers in self._grabbed:
                for lock in self._lock_masks:
                    self._root.ungrab_key(keycode, modifiers | lock)
            self._display.sync()
        self._grabbed = []

    def stop(self) -> None:
        if self._notifier is not None:
            self._notifier.setEnabled(False)
            self._notifier = None
        if self._display is not None:
            self._ungrab_all()
            with suppress(Exception):
                self._display.close()
            self._display = None
        self.available = False
