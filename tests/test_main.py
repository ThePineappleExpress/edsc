"""Tests for the command-line entry point."""

# SPDX-License-Identifier: GPL-3.0-or-later

import os
import sys

import pytest

from edsc import trace
from edsc.__main__ import _apply_cli, _prefer_xcb, main
from edsc.gui.controller_tester import development_mode_enabled

_SWITCHES = ("EDSC_DEV", "EDSC_TRACE")


@pytest.fixture(autouse=True)
def _no_dev_switches():
    """Clear both switches around each test, restoring whatever was there: ``_apply_cli`` exports into ``os.environ`` itself, which monkeypatch can't undo for a variable that started unset, so leaving dev mode on would leak into every later test."""
    saved = {var: os.environ.get(var) for var in _SWITCHES}
    for var in _SWITCHES:
        os.environ.pop(var, None)
    yield
    for var, value in saved.items():
        if value is None:
            os.environ.pop(var, None)
        else:
            os.environ[var] = value


def test_dev_flag_turns_on_both_development_features():
    _apply_cli(["--dev"])
    assert trace.enabled()
    assert development_mode_enabled()


def test_development_mode_stays_off_without_the_flag():
    _apply_cli([])
    assert not trace.enabled()
    assert not development_mode_enabled()


def test_qt_arguments_pass_through_untouched():
    # QApplication reads these off sys.argv itself, so parsing must not reject an argument just because it isn't one of ours.
    _apply_cli(["-platform", "offscreen", "--dev"])
    assert development_mode_enabled()


def test_unknown_arguments_do_not_abort_the_launch():
    _apply_cli(["--not-a-flag"])
    assert not development_mode_enabled()


def test_main_applies_flags_then_hands_off_to_the_gui(monkeypatch):
    from edsc.gui import app as app_module

    # ``main`` resolves ``run`` from the module at call time, so patching the attribute intercepts the launch without a QApplication ever starting.
    monkeypatch.setattr(app_module, "run", lambda: 7)
    assert main(["--dev"]) == 7
    assert development_mode_enabled()


#  Qt platform selection


def test_prefer_xcb_never_overrides_a_chosen_platform(monkeypatch):
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    _prefer_xcb()
    assert os.environ["QT_QPA_PLATFORM"] == "offscreen"


def test_prefer_xcb_pins_xcb_only_when_x_is_present(monkeypatch):
    monkeypatch.delenv("QT_QPA_PLATFORM", raising=False)
    monkeypatch.setenv("DISPLAY", ":1")
    _prefer_xcb()
    assert os.environ["QT_QPA_PLATFORM"] == "xcb"

    monkeypatch.delenv("QT_QPA_PLATFORM")
    monkeypatch.delenv("DISPLAY")
    _prefer_xcb()
    assert "QT_QPA_PLATFORM" not in os.environ


def test_prefer_xcb_leaves_windows_alone(monkeypatch):
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.delenv("QT_QPA_PLATFORM", raising=False)
    monkeypatch.setenv("DISPLAY", ":1")  # e.g. an X server for other tools
    _prefer_xcb()
    assert "QT_QPA_PLATFORM" not in os.environ
