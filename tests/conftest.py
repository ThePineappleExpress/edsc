"""Shared suite setup: the offscreen Qt platform and the one QApplication."""

# SPDX-License-Identifier: GPL-3.0-or-later

import os

# pytest imports this conftest before any test module, so the platform is set before the first QApplication anywhere -- no test file may rely on a sibling having set it first (that was collection-order dependent).
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest


@pytest.fixture(scope="session")
def qapp():
    """The process-wide QApplication; Qt allows only one, so it is shared."""
    from PySide6.QtWidgets import QApplication

    yield QApplication.instance() or QApplication([])
