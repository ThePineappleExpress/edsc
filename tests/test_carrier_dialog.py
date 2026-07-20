"""Tests for the fleet-carrier cargo correction dialog."""

# SPDX-License-Identifier: GPL-3.0-or-later

import pytest
from PySide6.QtWidgets import QDialog, QDialogButtonBox

from edsc.gui.carrier_dialog import CarrierCargoDialog
from edsc.model import CommodityRow


def _row(key, name, on_carrier):
    return CommodityRow(key=key, name=name, required=100, provided=0,
                        in_cargo=0, remaining=100, short=100,
                        on_carrier=on_carrier)


@pytest.fixture
def dialog(qapp):
    d = CarrierCargoDialog([_row("steel", "Steel", 250), _row("gold", "Gold", 0)])
    yield d
    d.deleteLater()
    qapp.processEvents()


def test_one_editor_per_row_seeded_with_the_tracked_amount(dialog):
    assert dialog.values() == {"steel": 250, "gold": 0}


def test_edited_values_come_back_keyed_by_commodity(dialog):
    dialog._spins["steel"].setValue(4000)
    assert dialog.values() == {"steel": 4000, "gold": 0}


def test_amounts_cannot_go_negative(dialog):
    dialog._spins["gold"].setValue(-5)
    assert dialog.values()["gold"] == 0


def test_ok_accepts_and_cancel_rejects(qapp):
    accepted = CarrierCargoDialog([_row("steel", "Steel", 1)])
    buttons = accepted.findChild(QDialogButtonBox)
    buttons.button(QDialogButtonBox.Ok).click()
    assert accepted.result() == QDialog.Accepted

    rejected = CarrierCargoDialog([_row("steel", "Steel", 1)])
    buttons = rejected.findChild(QDialogButtonBox)
    buttons.button(QDialogButtonBox.Cancel).click()
    assert rejected.result() == QDialog.Rejected

    for d in (accepted, rejected):
        d.deleteLater()
    qapp.processEvents()
