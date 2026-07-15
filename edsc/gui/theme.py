"""Shared colours, metrics, fonts, and styles for the complete EDSC GUI.

The stock palette matches the game's default orange HUD.  When the player has
recoloured their HUD (``MatrixRed``/``MatrixGreen``/``MatrixBlue`` in the
graphics configuration), :func:`apply_hud_matrix` re-derives every colour
through the same matrix so the overlay follows suit.


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

from dataclasses import dataclass
from typing import TYPE_CHECKING

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QFont, QGuiApplication, QPalette

if TYPE_CHECKING:
    from PySide6.QtWidgets import QApplication, QTableView, QToolButton, QWidget

from ..hud_colors import Matrix, transform


@dataclass(frozen=True)
class ThemeMetrics:
    """Non-colour visual constants shared by all GUI components."""

    overlay_outer_margins: tuple[int, int, int, int] = (6, 6, 6, 6)
    panel_margins: tuple[int, int, int, int] = (12, 10, 12, 10)
    page_margins: tuple[int, int, int, int] = (0, 0, 0, 0)
    content_spacing: int = 6
    header_spacing: int = 0
    window_control_size: tuple[int, int] = (24, 22)
    table_row_height: int = 20
    table_height_cap: int = 10_000
    station_system_width_fallback: int = 60
    station_name_width_fallback: int = 40
    station_column_slack: int = 8
    table_cell_padding: int = 10
    table_header_extra: int = 12
    auto_height_minimum: int = 60
    auto_height_screen_fraction: float = 0.8
    auto_height_chrome: int = 180
    settings_dialog_minimum_width: int = 460
    carrier_dialog_minimum_width: int = 360
    opacity_percent_minimum: int = 0
    opacity_percent_maximum: int = 100
    font_point_minimum: int = 7
    font_point_maximum: int = 20


METRICS = ThemeMetrics()

# Object names used as stylesheet roles. Keeping the selectors here prevents
# widgets from having to know how a role is represented in QSS.
PANEL_ROLE = "panel"
TITLE_ROLE = "title"
SUBTITLE_ROLE = "subtitle"
STATUS_ROLE = "status"
CREDIT_ROLE = "credit"
MUTED_ROLE = "muted"
COMPLETE_BUTTON_ROLE = "completeButton"
WINDOW_CONTROL_ROLE = "windowControl"

# Stock (identity-matrix) palette. The module globals declared below are
# re-derived from these whenever a HUD colour matrix is applied.
_BASE: dict[str, tuple[int, int, int]] = {
    "ORANGE": (255, 130, 20),
    "ORANGE_DIM": (180, 92, 14),
    "TEXT": (235, 232, 226),
    "TEXT_DIM": (150, 146, 138),
    "DONE": (64, 224, 96),         # delivered in full
    "READY": (255, 208, 32),       # carrying enough to finish this line
    "SHORT": (255, 72, 64),        # still need to acquire more
    "GRID": (60, 54, 44),
    # Stylesheet-only shades (panel, buttons, tabs, highlights).
    "PANEL_BG": (0, 0, 0),
    "BG": (5, 5, 5),
    "BG_TAB": (30, 26, 20),
    "BG_ACTIVE": (25, 13, 2),
    "BG_CHECKED": (107, 58, 8),
    "DONE_BG": (26, 40, 30),
    "DONE_BG_HOVER": (34, 62, 42),
    "ALT_ROW": (255, 210, 160),
}

# Traffic-light status colours keep their stock values regardless of the HUD
# matrix: they encode delivered/ready/short, and a matrix tuned for the game's
# orange chrome happily crushes them to near-white -- or, for a red<->green
# swap, inverts their meaning outright.
_SEMANTIC = frozenset({"DONE", "READY", "SHORT", "DONE_BG", "DONE_BG_HOVER"})

ORANGE: QColor
ORANGE_DIM: QColor
TEXT: QColor
TEXT_DIM: QColor
DONE: QColor
READY: QColor
SHORT: QColor
GRID: QColor
PANEL_BG: QColor
BG: QColor
BG_TAB: QColor
BG_ACTIVE: QColor
BG_CHECKED: QColor
DONE_BG: QColor
DONE_BG_HOVER: QColor
ALT_ROW: QColor


def apply_hud_matrix(matrix: Matrix | None) -> None:
    """Re-derive the palette through the game's HUD colour matrix.

    ``None`` (or the identity matrix) restores the stock orange palette.
    Stylesheets and the application palette need to be rebuilt afterwards;
    colours read per-paint (e.g. by the table models) pick this up on their own.
    """
    for name, rgb in _BASE.items():
        if matrix is not None and name not in _SEMANTIC:
            rgb = transform(matrix, rgb)
        globals()[name] = QColor(*rgb)


apply_hud_matrix(None)


def _rgba(c: QColor, alpha: int) -> str:
    return f"rgba({c.red()},{c.green()},{c.blue()},{alpha})"


def set_role(widget: QWidget, role: str) -> None:
    """Assign a theme-owned stylesheet role to ``widget``."""
    widget.setObjectName(role)


def configure_window_control(button: QToolButton) -> None:
    """Apply the compact, uniform presentation for overlay window controls."""
    set_role(button, WINDOW_CONTROL_ROLE)
    button.setFixedSize(*METRICS.window_control_size)


def monospace_font(point_size: int | None = None) -> QFont:
    """Return the theme's fixed-width font, optionally at ``point_size``."""
    font = QFont("monospace")
    font.setStyleHint(QFont.Monospace)
    if point_size is not None:
        font.setPointSize(point_size)
    return font


def resized_font(base: QFont, point_size: int) -> QFont:
    """Copy ``base`` and apply the configured point size."""
    font = QFont(base)
    font.setPointSize(point_size)
    return font


def configure_table(table: QTableView, *, elide_text: bool = False) -> None:
    """Apply the shared visual presentation used by overlay tables."""
    table.setShowGrid(False)
    table.setAlternatingRowColors(True)
    table.verticalHeader().setDefaultSectionSize(METRICS.table_row_height)
    table.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
    if elide_text:
        table.setWordWrap(False)
        table.setTextElideMode(Qt.ElideRight)


def application_palette() -> QPalette:
    """Build the native-widget palette from the current HUD-adjusted colours."""
    palette = QPalette()
    palette.setColor(QPalette.Window, BG)
    palette.setColor(QPalette.WindowText, ORANGE)
    palette.setColor(QPalette.Base, PANEL_BG)
    palette.setColor(QPalette.AlternateBase, BG_TAB)
    palette.setColor(QPalette.ToolTipBase, BG)
    palette.setColor(QPalette.ToolTipText, ORANGE)
    palette.setColor(QPalette.Text, ORANGE)
    palette.setColor(QPalette.Button, BG_TAB)
    palette.setColor(QPalette.ButtonText, ORANGE_DIM)
    palette.setColor(QPalette.BrightText, ORANGE)
    palette.setColor(QPalette.Highlight, ORANGE)
    palette.setColor(QPalette.HighlightedText, BG)
    palette.setColor(QPalette.Link, ORANGE)
    palette.setColor(QPalette.PlaceholderText, TEXT_DIM)
    palette.setColor(QPalette.Disabled, QPalette.Text, TEXT_DIM)
    palette.setColor(QPalette.Disabled, QPalette.WindowText, TEXT_DIM)
    palette.setColor(QPalette.Disabled, QPalette.ButtonText, TEXT_DIM)
    return palette


def application_stylesheet(font_pt: int) -> str:
    """Stylesheet for dialogs, menus, tooltips, and native input widgets."""
    return f"""
    QDialog, QMenu {{
        background-color: {BG.name()}; color: {ORANGE.name()};
        font-size: {font_pt}pt;
    }}
    QDialog QLabel {{ color: {ORANGE.name()}; }}
    QLabel#{MUTED_ROLE} {{ color: {ORANGE_DIM.name()}; }}
    QLineEdit, QSpinBox {{
        background-color: {PANEL_BG.name()}; color: {ORANGE.name()};
        padding: 3px 6px; selection-background-color: {ORANGE.name()};
        selection-color: {BG.name()};
    }}
    QLineEdit:focus, QSpinBox:focus {{ border-color: {ORANGE.name()}; }}
    QPushButton {{
        color: {BG_TAB.name()}; background-color: {_rgba(BG, 180)};
        border: none; padding: 3px 10px; font-weight: 600;
    }}
    QPushButton:hover {{
        color: {TEXT.name()}; background-color: {_rgba(ORANGE_DIM, 220)};
    }}
    QPushButton:pressed, QPushButton:default {{
        color: {BG.name()}; background-color: {_rgba(ORANGE, 220)};
    }}
    QCheckBox {{ color: {ORANGE.name()}; spacing: 6px; }}
    QSlider::groove:horizontal {{
        background-color: {GRID.name()}; height: 4px; border-radius: 2px;
    }}
    QSlider::sub-page:horizontal {{
        background-color: {ORANGE_DIM.name()}; border-radius: 2px;
    }}
    QSlider::handle:horizontal {{
        background-color: {ORANGE.name()}; width: 14px; margin: -5px 0;
        border-radius: 7px;
    }}
    QScrollArea {{
        background-color: {PANEL_BG.name()}; border: 1px solid {GRID.name()};
    }}
    QScrollArea QWidget {{ background-color: {PANEL_BG.name()}; }}
    QMenu {{ border: 1px solid {GRID.name()}; padding: 3px; }}
    QMenu::item {{ padding: 4px 22px 4px 8px; }}
    QMenu::item:selected {{
        background-color: {ORANGE.name()}; color: {BG.name()};
    }}
    QToolTip {{
        background-color: {BG.name()}; color: {ORANGE.name()};
        padding: 10px; border: none;
    }}
    QScrollBar:vertical {{ background: transparent; width: 8px; }}
    QScrollBar:horizontal {{ background: transparent; height: 8px; }}
    QScrollBar::handle {{ background: {_rgba(ORANGE_DIM, 150)}; min-height: 18px; }}
    QScrollBar::add-line, QScrollBar::sub-line {{ width: 0; height: 0; }}
    """


def apply_application_theme(application: QApplication, font_pt: int) -> None:
    """Apply the current theme to every top-level Qt window and popup."""
    application.setStyle("Fusion")
    application.setPalette(application_palette())
    application.setStyleSheet(application_stylesheet(font_pt))


def tooltip_base_pt() -> int:
    """Point size tooltips render at: the anchor for relative rich-text sizes."""
    app = QGuiApplication.instance()
    size = app.font().pointSize() if app is not None else -1
    return size if size > 0 else 9


def tooltip_icon_px() -> int:
    """Pixel size of the station-type sprite in tooltips (two title lines)."""
    return 2 * (tooltip_base_pt() + 2)


def tooltip_station_header(
    icon: str,
    name: str,
    freshness: str,
    system: str,
    kind: str,
    owner: str = "",
) -> str:
    """Tooltip identity block: market freshness in fine print on top, then the
    type sprite beside the bold station name - with the proprietor line (the
    controlling minor faction) two points smaller beneath it when given - and
    the system and station-kind lines. Inputs are already-escaped rich text;
    ``icon`` is an inline ``<img>`` tag.
    """
    base = tooltip_base_pt()
    title_pt, sub_pt, fine_pt = base + 2, base - 1, base - 2
    sub = f"color: {ORANGE.name()}; font-size: {sub_pt}pt"
    owner_line = (
        f"<br><span style='color: {ORANGE.name()}; "
        f"font-size: {title_pt - 2}pt'>{owner}</span>"
        if owner
        else ""
    )
    return (
        f"<span style='font-size: {fine_pt}pt'>{freshness}</span>"
        "<table cellspacing='0' cellpadding='0'><tr>"
        f"<td valign='middle'>{icon}</td>"
        f"<td valign='middle' style='padding-left: 6px'>"
        f"<span style='color: {ORANGE_DIM.name()}; font-size: {title_pt}pt; "
        f"font-weight: bold'>{name}</span>{owner_line}</td>"
        "</tr></table>"
        f"<span style='{sub}'>{system}</span><br>"
        f"<span style='{sub}'>{kind}</span>"
    )


def tooltip_stock_table(
    stock: list[tuple[str, str, bool]],
    missing: list[str],
) -> str:
    """Stocked/missing commodity lists as a borderless rich-text table.

    Stocked entries are (escaped name, stocked-percent text, fully-stocked)
    triples, the percent right-aligned in its own column, rendered green when
    fully stocked and yellow otherwise; missing entries are plain escaped
    names rendered red. The Missing side is left out entirely when nothing
    is missing.
    """
    green = DONE.name()
    yellow = READY.name()
    red = SHORT.name()
    gap = "padding-left: 16px; "
    headings = f"<th colspan='2' align='left' style='color: {green}'>Stock</th>"
    if missing:
        headings += f"<th align='left' style='{gap}color: {red}'>Missing</th>"
    rows = []
    for i in range(max(len(stock), len(missing))):
        cells = []
        if i < len(stock):
            name, pct, full = stock[i]
            colour = green if full else yellow
            cells.append(f"<td style='color: {colour}'>{name}</td>")
            cells.append(
                f"<td align='right' style='padding-left: 8px; "
                f"color: {colour}'>{pct}</td>"
            )
        else:
            cells.append("<td></td><td></td>")
        if missing:
            if i < len(missing):
                cells.append(f"<td style='{gap}color: {red}'>{missing[i]}</td>")
            else:
                cells.append("<td></td>")
        rows.append(f"<tr>{''.join(cells)}</tr>")
    return (
        "<table cellspacing='0' cellpadding='1'>"
        f"<tr>{headings}</tr>{''.join(rows)}</table>"
    )


def panel_stylesheet(alpha: int, font_pt: int) -> str:
    """Stylesheet for the overlay's inner panel at a given background alpha."""
    return f"""
    QFrame#{PANEL_ROLE} {{
        background-color: {_rgba(PANEL_BG, alpha)};
    }}
    QLabel {{ color: {ORANGE.name()}; font-size: {font_pt}pt; }}
    QLabel#{TITLE_ROLE} {{ color: {ORANGE_DIM.name()}; font-size: {font_pt + 4}pt; font-weight: 900; }}
    QLabel#{SUBTITLE_ROLE} {{ color: {ORANGE.name()}; font-size: {font_pt}pt; }}
    QLabel#{STATUS_ROLE} {{ color: {ORANGE_DIM.name()}; font-size: {font_pt - 2}pt; }}
    QLabel#{CREDIT_ROLE} {{ color: {ORANGE_DIM.name()}; font-size: {font_pt - 2}pt; }}
    QTabBar {{ qproperty-drawBase: 0; }}
    QTabBar::tab {{
        color: {BG_TAB.name()}; background: {_rgba(BG, 180)};
        padding: 3px 10px; margin-right: 2px; font-size: {font_pt}pt; font-weight: 600;
        max-width: 140px;
    }}
    QTabBar::tab:selected {{
        color: {BG.name()}; background: {_rgba(ORANGE, 220)};
    }}
    QTabBar::tab:hover {{
        color: {TEXT.name()}; background: {_rgba(ORANGE_DIM, 220)};
    }}
    QTabBar QToolButton {{
        background: {_rgba(BG_TAB, 220)}; color: {ORANGE.name()};
    }}
    QToolButton {{
        color: {BG_TAB.name()}; background: {_rgba(BG, 180)};
        border: none; padding: 3px 10px;
        font-size: {font_pt}pt; font-weight: 600;
    }}
    QToolButton:hover {{
        color: {TEXT.name()}; background: {_rgba(ORANGE_DIM, 220)};
    }}
    QToolButton:checked, QToolButton:pressed {{
        color: {BG.name()}; background: {_rgba(ORANGE, 220)};
    }}
    QToolButton#{WINDOW_CONTROL_ROLE} {{
        color: {BG_TAB.name()}; background: {_rgba(BG, 180)};
        border: none; padding: 0;
        font-size: {max(1, font_pt - 1)}pt; font-weight: 600;
    }}
    QToolButton#{WINDOW_CONTROL_ROLE}:hover {{
        color: {TEXT.name()}; background: {_rgba(ORANGE_DIM, 220)};
    }}
    QToolButton#{WINDOW_CONTROL_ROLE}:checked,
    QToolButton#{WINDOW_CONTROL_ROLE}:pressed {{
        color: {BG.name()}; background: {_rgba(ORANGE, 220)};
    }}
    QToolButton#{COMPLETE_BUTTON_ROLE} {{
        color: {DONE.name()}; background: {_rgba(DONE_BG, 180)};
        padding: 4px 8px; font-weight: 600;
    }}
    QToolButton#{COMPLETE_BUTTON_ROLE}:hover {{ background: {_rgba(DONE_BG_HOVER, 220)}; }}
    QProgressBar {{
        background: {_rgba(BG, 160)}; border: none;
        height: 6px; text-align: center; color: transparent;
    }}
    QProgressBar::chunk {{ background-color: {ORANGE_DIM.name()}; }}
    QTableView {{
        color: {ORANGE.name()}; background-color: {_rgba(BG, 20)}; gridline-color: {GRID.name()};
        font-size: {font_pt}pt; selection-background-color: transparent;
        outline: none;
        /* Faint warm lightening of every other row; just enough to follow a
           line across a busy list without shouting. */
        alternate-background-color: {_rgba(ORANGE_DIM, 14)};
    }}
    QHeaderView::section {{
        background: {_rgba(BG, 200)}; color: {ORANGE_DIM.name()};
        border: none; padding: 3px 6px; font-size: {font_pt - 1}pt;
    }}
    QScrollBar:vertical {{ background: transparent; width: 8px; }}
    QScrollBar::handle:vertical {{ background: {_rgba(ORANGE_DIM, 120)}; }}
    QScrollBar::add-line, QScrollBar::sub-line {{ height: 0; }}
    """
