"""Manual correction of tracked fleet-carrier amounts.
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
