"""Tests for the small widgets shared by the overlay and settings dialog."""

# SPDX-License-Identifier: GPL-3.0-or-later

import sys

import pytest
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QLabel

from edsc.gui import widgets
from edsc.gui.widgets import flash_status

_FAST_FLASH_MS = 50
_PAST_FLASH_MS = _FAST_FLASH_MS + 200


@pytest.fixture(autouse=True)
def fast_flash(monkeypatch):
    """Shrink the 2.5 s notice window: waited out in real time it dominated the whole suite's runtime, and ``flash_status`` reads the duration at call time so nothing else changes."""
    monkeypatch.setattr(widgets, "_FLASH_MS", _FAST_FLASH_MS)


@pytest.fixture
def unhandled(monkeypatch):
    """Collect exceptions Qt would otherwise only print from the event loop."""
    errors = []
    monkeypatch.setattr(sys, "excepthook", lambda *exc: errors.append(exc[1]))
    return errors


def test_a_notice_replaces_the_text_and_then_gives_it_back(qapp):
    label = QLabel("Cached 3 orbital")

    flash_status(label, "Copied 'Sol' to clipboard")
    assert label.text() == "Copied 'Sol' to clipboard"

    QTest.qWait(_PAST_FLASH_MS)
    assert label.text() == "Cached 3 orbital"


def test_a_notice_never_clobbers_text_that_replaced_it(qapp):
    """A search finishing mid-notice owns the status line; the restore defers."""
    label = QLabel("Cached 3 orbital")
    flash_status(label, "Copied 'Sol' to clipboard")

    label.setText("Searching Spansh near Sol…")
    QTest.qWait(_PAST_FLASH_MS)

    assert label.text() == "Searching Spansh near Sol…"


def test_the_restore_is_dropped_when_the_label_dies_first(qapp, unhandled):
    """Copying a system then quitting must not restore onto a deleted widget."""
    label = QLabel("Cached 3 orbital")
    flash_status(label, "Copied 'Sol' to clipboard")

    label.deleteLater()
    qapp.processEvents()
    del label
    QTest.qWait(_PAST_FLASH_MS)

    assert unhandled == []
