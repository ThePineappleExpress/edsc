"""Manual correction of tracked fleet-carrier amounts.


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

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QLabel,
    QScrollArea,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from ..model import CommodityRow


class CarrierCargoDialog(QDialog):
    def __init__(self, rows: list[CommodityRow], parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Set fleet-carrier cargo")
        self.setMinimumWidth(360)
        self._spins: dict[str, QSpinBox] = {}

        layout = QVBoxLayout(self)
        info = QLabel(
            "Enter how much of each commodity is on fleet carrier. "
            "Transfers made while EDSC runs keep these up to date."
        )
        info.setWordWrap(True)
        layout.addWidget(info)

        form_host = QWidget()
        form = QFormLayout(form_host)
        for r in rows:
            spin = QSpinBox()
            spin.setRange(0, 1_000_000)
            spin.setSingleStep(1)
            spin.setValue(int(r.on_carrier))
            spin.setSuffix(" t")
            self._spins[r.key] = spin
            form.addRow(r.name, spin)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(form_host)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        layout.addWidget(scroll, 1)

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def values(self) -> dict[str, int]:
        """Commodity key -> amount entered by the user."""
        return {key: spin.value() for key, spin in self._spins.items()}
