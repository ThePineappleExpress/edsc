"""The Colonize tab's filter deck: sliders, dropdowns and toggle groups, below the results table (list on top, filters below); numeric refinements are full-row sliders, enumerations dropdowns, boolean/presence refinements toggle buttons grouped by axis under a plain label, each group flowing through a :class:`FlowLayout` so buttons wrap to fit the window width and stretch to fill their row. Every control except the search radius refines the cached pool client-side, so the widget distinguishes :attr:`changed` (a live filter moved -- re-slice existing results) from :attr:`searchRequested` (the radius' companion re-query -- run a fresh Spansh search)."""

# SPDX-License-Identifier: GPL-3.0-or-later

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QComboBox,
    QHBoxLayout,
    QLabel,
    QSlider,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from ..config import COLONIZE_RANGE_MAX, COLONIZE_RANGE_MIN, Config
from ..systems import MAX_STEPS, RING_TYPES, SystemFilters
from . import theme
from .flow_layout import FlowLayout
from .widgets import tool_button

# toggle key -> button label, per band; body/star keys feed systems.BODY_TYPE_MATCH / STAR_KIND_MATCH, ring keys are exact Spansh classes.
_STATE_TOGGLES = (
    ("terra", "Terraformable"),
    ("claim", "Claimable now"),
    ("verif", "Verified only"),
)
_BODY_TOGGLES = (
    ("ELW", "Earth-like"),
    ("WW", "Water"),
    ("AW", "Ammonia"),
    ("HMC", "HMC"),
    ("MR", "Metal-rich"),
    ("GG", "Gas giant"),
)
_STAR_TOGGLES = (
    ("scoop", "Scoopable"),
    ("WD", "White dwarf"),
    ("NS", "Neutron"),
    ("BH", "Black hole"),
)
_RING_TOGGLES = tuple((rt, rt) for rt in RING_TYPES)

_SORTS = (
    ("Balanced", "balanced"),
    ("Nearest", "nearest"),
    ("Most bodies", "bodies"),
)
_STARS = (("Any", 1), ("≥ 2 stars", 2), ("≥ 3 stars", 3), ("≥ 4 stars", 4))

_BODIES_MAX = 30  # slider cap; the value is a floor, so "30" means ">= 30"


class ColonizeFilters(QWidget):
    """Filter deck for the Colonize tab, backed by (and persisting to) config."""

    changed = Signal()  # a live (non-radius) filter changed -> re-filter the pool
    searchRequested = Signal()  # the radius' companion re-query button was pressed
    rangeEdited = Signal()  # the radius slider moved (re-query is deferred to search)

    def __init__(self, config: Config, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.config = config
        self._toggles: dict[str, QToolButton] = {}
        self._build()
        self.sync_from_config()

    #  construction

    def _build(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(*theme.METRICS.page_margins)
        root.setSpacing(theme.METRICS.content_spacing)

        # --- sliders (one per row) ---
        self._range = QSlider(Qt.Horizontal)
        self._range.setRange(COLONIZE_RANGE_MIN, COLONIZE_RANGE_MAX)
        self._range.setToolTip(
            f"Search radius around your current system "
            f"({COLONIZE_RANGE_MIN}-{COLONIZE_RANGE_MAX} Ly). Wide radii are for "
            "planning bridge-colony chains; changing it re-queries Spansh, so "
            "press ↻ Search to apply."
        )
        self._range_val = QLabel()
        self._refresh_btn = tool_button(
            "↻ Search", "Run a fresh Spansh search at the chosen radius"
        )
        self._refresh_btn.clicked.connect(self.searchRequested)
        self._range.valueChanged.connect(self._on_range_edited)
        radius_row = self._slider_row(
            "Search radius", self._range, self._range_val, trailing=self._refresh_btn
        )
        root.addLayout(radius_row)

        self._bodies = QSlider(Qt.Horizontal)
        self._bodies.setRange(0, _BODIES_MAX)
        self._bodies.setToolTip("Keep only systems with at least this many bodies")
        self._bodies_val = QLabel()
        self._bodies.valueChanged.connect(lambda: self._on_live_change(self._bodies_labels))
        root.addLayout(self._slider_row("Min bodies", self._bodies, self._bodies_val))

        self._hops = QSlider(Qt.Horizontal)
        self._hops.setRange(1, MAX_STEPS)
        self._hops.setToolTip(
            "Colonisation steps to reach the system: 1 is claimable now, higher "
            "allows longer bridge-colony chains"
        )
        self._hops_val = QLabel()
        self._hops.valueChanged.connect(lambda: self._on_live_change(self._hops_labels))
        root.addLayout(self._slider_row("Max bridges", self._hops, self._hops_val))

        # --- dropdowns (up to 4 per row) ---
        self._sort = self._combo(_SORTS)
        self._stars = self._combo(_STARS)
        self._sort.currentIndexChanged.connect(lambda: self._on_live_change())
        self._stars.currentIndexChanged.connect(lambda: self._on_live_change())
        drops = QHBoxLayout()
        drops.setSpacing(theme.METRICS.content_spacing)
        drops.addWidget(self._labelled("Sort", self._sort), 1)
        drops.addWidget(self._labelled("Min stars", self._stars), 1)
        root.addLayout(drops)

        # --- toggle groups (grouped by axis, wrapping button rows) ---
        self._section(root, "Must have", _STATE_TOGGLES)
        self._section(root, "Contains body", _BODY_TOGGLES, note="known / scanned")
        self._section(root, "Star type", _STAR_TOGGLES)
        self._section(root, "Rings / belts", _RING_TOGGLES, note="needs body lookup")

    @staticmethod
    def _slider_row(
        label: str,
        slider: QSlider,
        value: QLabel,
        trailing: QWidget | None = None,
    ) -> QHBoxLayout:
        row = QHBoxLayout()
        row.setSpacing(theme.METRICS.content_spacing)
        name = QLabel(label)
        name.setMinimumWidth(78)
        row.addWidget(name)
        row.addWidget(slider, 1)
        value.setMinimumWidth(64)
        value.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        row.addWidget(value)
        if trailing is not None:
            row.addWidget(trailing)
        return row

    @staticmethod
    def _combo(options: tuple[tuple[str, object], ...]) -> QComboBox:
        combo = QComboBox()
        for label, value in options:
            combo.addItem(label, value)
        return combo

    @staticmethod
    def _labelled(text: str, field: QWidget) -> QWidget:
        wrap = QWidget()
        col = QVBoxLayout(wrap)
        col.setContentsMargins(0, 0, 0, 0)
        col.setSpacing(2)
        caption = QLabel(text)
        theme.set_role(caption, theme.MUTED_ROLE)
        col.addWidget(caption)
        col.addWidget(field)
        return wrap

    def _section(
        self,
        root: QVBoxLayout,
        title: str,
        toggles: tuple[tuple[str, str], ...],
        note: str = "",
    ) -> None:
        """A plain-labelled group of toggle buttons that wrap and fill the width."""
        caption = QLabel(f"{title}     {note}" if note else title)
        theme.set_role(caption, theme.MUTED_ROLE)
        root.addWidget(caption)

        flow = FlowLayout(spacing=theme.METRICS.content_spacing)
        for key, label in toggles:
            btn = tool_button(label, f"Require: {label}", checkable=True)
            btn.toggled.connect(lambda _=False: self._on_live_change())
            flow.addWidget(btn)
            self._toggles[key] = btn
        root.addLayout(flow)

    #  live state

    def _on_range_edited(self) -> None:
        self._range_labels()
        self.config.colonize_range_ly = self._range.value()
        self.rangeEdited.emit()

    def _on_live_change(self, relabel=None) -> None:
        if relabel is not None:
            relabel()
        self._persist()
        self.changed.emit()

    #  label writers

    def _range_labels(self) -> None:
        self._range_val.setText(f"{self._range.value()} Ly")

    def _bodies_labels(self) -> None:
        v = self._bodies.value()
        self._bodies_val.setText("Any" if v == 0 else f"≥ {v}")

    def _hops_labels(self) -> None:
        v = self._hops.value()
        if v >= MAX_STEPS:
            self._hops_val.setText("Any")
        elif v == 1:
            self._hops_val.setText("1 · claim")
        else:
            self._hops_val.setText(f"≤ {v} bridges")

    #  public API

    def range_ly(self) -> int:
        return self._range.value()

    def set_range_ly(self, value: int) -> None:
        self._range.blockSignals(True)
        self._range.setValue(int(value))
        self._range.blockSignals(False)
        self._range_labels()

    def sort(self) -> str:
        return self._sort.currentData()

    def system_filters(self) -> SystemFilters:
        checked = {k: b.isChecked() for k, b in self._toggles.items()}
        return SystemFilters(
            min_bodies=self._bodies.value(),
            max_hops=self._hops.value(),
            min_stars=self._stars.currentData(),
            terraformable_only=checked["terra"],
            claimable_only=checked["claim"],
            verified_only=checked["verif"],
            body_types=tuple(k for k, _ in _BODY_TOGGLES if checked[k]),
            star_types=tuple(k for k, _ in _STAR_TOGGLES if checked[k]),
            ring_types=tuple(k for k, _ in _RING_TOGGLES if checked[k]),
        )

    def set_controls_enabled(self, enabled: bool) -> None:
        self.setEnabled(enabled)

    def sync_from_config(self) -> None:
        """Load every control from config without emitting change signals."""
        c = self.config
        widgets = [
            self._range, self._bodies, self._hops, self._sort, self._stars,
            *self._toggles.values(),
        ]
        for w in widgets:
            w.blockSignals(True)
        try:
            self._range.setValue(int(c.colonize_range_ly))
            self._bodies.setValue(max(0, min(_BODIES_MAX, int(c.colonize_min_bodies))))
            self._hops.setValue(max(1, min(MAX_STEPS, int(c.colonize_max_hops))))
            self._select(self._sort, c.colonize_sort)
            self._select(self._stars, int(c.colonize_min_stars))
            active = (
                {"terra": c.colonize_terraformable_only,
                 "claim": c.colonize_claimable_only,
                 "verif": c.colonize_verified_only}
            )
            for key, btn in self._toggles.items():
                if key in active:
                    btn.setChecked(bool(active[key]))
                elif key in dict(_BODY_TOGGLES):
                    btn.setChecked(key in (c.colonize_body_types or []))
                elif key in dict(_STAR_TOGGLES):
                    btn.setChecked(key in (c.colonize_star_types or []))
                else:
                    btn.setChecked(key in (c.colonize_ring_types or []))
        finally:
            for w in widgets:
                w.blockSignals(False)
        self._range_labels()
        self._bodies_labels()
        self._hops_labels()

    @staticmethod
    def _select(combo: QComboBox, value: object) -> None:
        idx = combo.findData(value)
        combo.setCurrentIndex(idx if idx >= 0 else 0)

    def _persist(self) -> None:
        c = self.config
        f = self.system_filters()
        c.colonize_min_bodies = f.min_bodies
        c.colonize_max_hops = f.max_hops
        c.colonize_min_stars = f.min_stars
        c.colonize_terraformable_only = f.terraformable_only
        c.colonize_claimable_only = f.claimable_only
        c.colonize_verified_only = f.verified_only
        c.colonize_body_types = list(f.body_types)
        c.colonize_star_types = list(f.star_types)
        c.colonize_ring_types = list(f.ring_types)
        c.colonize_sort = self.sort()
