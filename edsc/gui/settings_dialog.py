"""Settings dialog: journal path override and overlay appearance."""

from __future__ import annotations

from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QSlider,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)
from PySide6.QtCore import Qt

from ..config import Config
from ..journal import locator


class SettingsDialog(QDialog):
    def __init__(self, config: Config, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("EDSC Settings")
        self.config = config
        self.setMinimumWidth(460)

        form = QFormLayout()

        # Journal directory override + auto-detect hint.
        self.journal_edit = QLineEdit(config.journal_dir)
        self.journal_edit.setPlaceholderText("(leave empty to auto-detect)")
        browse = QPushButton("Browse…")
        browse.clicked.connect(self._browse)
        path_row = QHBoxLayout()
        path_row.addWidget(self.journal_edit, 1)
        path_row.addWidget(browse)
        path_wrap = QWidget()
        path_wrap.setLayout(path_row)
        form.addRow("Journal folder:", path_wrap)

        detected = locator.find_journal_dir(config.journal_dir or None)
        hint = str(detected) if detected else "not found — set it manually above"
        detected_label = QLabel(f"Auto-detected: {hint}")
        detected_label.setWordWrap(True)
        detected_label.setStyleSheet("color: #96928a;")
        form.addRow("", detected_label)

        # Opacity.
        self.opacity = QSlider(Qt.Horizontal)
        self.opacity.setRange(30, 100)
        self.opacity.setValue(int(config.overlay_opacity * 100))
        self.opacity_label = QLabel(f"{self.opacity.value()}%")
        self.opacity.valueChanged.connect(
            lambda v: self.opacity_label.setText(f"{v}%")
        )
        op_row = QHBoxLayout()
        op_row.addWidget(self.opacity, 1)
        op_row.addWidget(self.opacity_label)
        op_wrap = QWidget()
        op_wrap.setLayout(op_row)
        form.addRow("Overlay opacity:", op_wrap)

        # Font size.
        self.font_size = QSpinBox()
        self.font_size.setRange(7, 20)
        self.font_size.setValue(config.font_point_size)
        form.addRow("Font size:", self.font_size)

        # Toggles.
        self.always_on_top = QCheckBox("Keep overlay above other windows")
        self.always_on_top.setChecked(config.always_on_top)
        form.addRow("", self.always_on_top)

        self.auto_height = QCheckBox("Auto-fit height to the commodity list")
        self.auto_height.setChecked(config.auto_height)
        form.addRow("", self.auto_height)

        self.auto_click_through = QCheckBox(
            "Click-through while the game window is focused"
        )
        self.auto_click_through.setChecked(config.auto_click_through)
        form.addRow("", self.auto_click_through)

        # Substrings used to recognise the game window (class or title).
        self.matchers = QLineEdit(", ".join(config.game_window_matchers))
        self.matchers.setPlaceholderText("steam_app_359320, elite - dangerous (client)")
        form.addRow("Game window matches:", self.matchers)

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)

        layout = QVBoxLayout(self)
        layout.addLayout(form)
        layout.addWidget(buttons)

    def _browse(self) -> None:
        start = self.journal_edit.text() or ""
        chosen = QFileDialog.getExistingDirectory(self, "Select journal folder", start)
        if chosen:
            self.journal_edit.setText(chosen)

    def apply_to(self, config: Config) -> None:
        """Write the dialog's values back into ``config``."""
        config.journal_dir = self.journal_edit.text().strip()
        config.overlay_opacity = self.opacity.value() / 100.0
        config.font_point_size = self.font_size.value()
        config.always_on_top = self.always_on_top.isChecked()
        config.auto_height = self.auto_height.isChecked()
        config.auto_click_through = self.auto_click_through.isChecked()
        config.game_window_matchers = [
            m.strip() for m in self.matchers.text().split(",") if m.strip()
        ]
