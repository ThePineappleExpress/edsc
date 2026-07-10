"""Shared colours and style for the overlay -- Elite Dangerous HUD palette.

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

from PySide6.QtGui import QColor

from ..hud_colors import Matrix, transform

# Stock (identity-matrix) palette. The module globals declared below are
# re-derived from these whenever a HUD colour matrix is applied.
_BASE: dict[str, tuple[int, int, int]] = {
    "ORANGE": (255, 130, 20),
    "ORANGE_DIM": (180, 92, 14),
    "TEXT": (235, 232, 226),
    "TEXT_DIM": (150, 146, 138),
    "DONE": (96, 176, 108),        # delivered in full
    "READY": (240, 196, 60),       # carrying enough to finish this line
    "SHORT": (226, 108, 92),       # still need to acquire more
    "GRID": (60, 54, 44),
    # Stylesheet-only shades (panel, buttons, tabs, highlights).
    "PANEL_BG": (12, 14, 18),
    "BG": (40, 34, 26),
    "BG_TAB": (30, 26, 20),
    "BG_ACTIVE": (90, 54, 14),
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
    Widgets styled via :func:`panel_stylesheet` need it re-applied afterwards;
    colours read per-paint (e.g. by the table models) pick this up on their own.
    """
    for name, rgb in _BASE.items():
        if matrix is not None and name not in _SEMANTIC:
            rgb = transform(matrix, rgb)
        globals()[name] = QColor(*rgb)


apply_hud_matrix(None)


def _rgba(c: QColor, alpha: int) -> str:
    return f"rgba({c.red()},{c.green()},{c.blue()},{alpha})"


def panel_stylesheet(alpha: int, font_pt: int) -> str:
    """Stylesheet for the overlay's inner panel at a given background alpha."""
    return f"""
    QFrame#panel {{
        background-color: {_rgba(PANEL_BG, alpha)};
        border: 1px solid {_rgba(ORANGE, 90)};
        border-radius: 10px;
    }}
    QLabel {{ color: {TEXT.name()}; font-size: {font_pt}pt; }}
    QLabel#title {{ color: {ORANGE.name()}; font-size: {font_pt + 2}pt; font-weight: 600; }}
    QLabel#subtitle {{ color: {TEXT_DIM.name()}; font-size: {font_pt - 1}pt; }}
    QLabel#status {{ color: {TEXT_DIM.name()}; font-size: {font_pt - 2}pt; }}
    QLabel#credit {{ color: {ORANGE_DIM.name()}; font-size: {font_pt - 2}pt; }}
    QTabBar {{ qproperty-drawBase: 0; }}
    QTabBar::tab {{
        color: {TEXT_DIM.name()}; background: {_rgba(BG_TAB, 180)};
        border: 1px solid {_rgba(ORANGE, 60)};
        border-top-left-radius: 4px; border-top-right-radius: 4px;
        padding: 3px 10px; margin-right: 2px; font-size: {font_pt - 1}pt;
        max-width: 140px;
    }}
    QTabBar::tab:selected {{
        color: {ORANGE.name()}; background: {_rgba(BG_ACTIVE, 220)};
        border-color: {ORANGE.name()};
    }}
    QTabBar::tab:hover {{ color: {TEXT.name()}; }}
    QTabBar QToolButton {{
        background: {_rgba(BG, 220)}; color: {ORANGE.name()};
        border: 1px solid {_rgba(ORANGE, 90)};
    }}
    QToolButton {{
        color: {TEXT.name()}; background: {_rgba(BG, 180)};
        border: 1px solid {_rgba(ORANGE, 90)}; border-radius: 4px;
        padding: 2px 6px; font-size: {font_pt - 1}pt;
    }}
    QToolButton:hover {{ background: {_rgba(BG_ACTIVE, 220)}; }}
    QToolButton:checked {{ background: {BG_CHECKED.name()}; border-color: {ORANGE.name()}; }}
    QToolButton#completeBtn {{
        color: {DONE.name()}; background: {_rgba(DONE_BG, 180)};
        border: 1px solid {_rgba(DONE, 140)};
        padding: 4px 8px; font-weight: 600;
    }}
    QToolButton#completeBtn:hover {{ background: {_rgba(DONE_BG_HOVER, 220)}; }}
    QProgressBar {{
        background: {_rgba(BG, 160)}; border: none; border-radius: 4px;
        height: 6px; text-align: center; color: transparent;
    }}
    QProgressBar::chunk {{ background-color: {ORANGE.name()}; border-radius: 4px; }}
    QTableView {{
        background: transparent; color: {TEXT.name()}; gridline-color: {GRID.name()};
        font-size: {font_pt}pt; selection-background-color: transparent;
        outline: none;
        /* Faint warm lightening of every other row; just enough to follow a
           line across a busy list without shouting. */
        alternate-background-color: {_rgba(ALT_ROW, 14)};
    }}
    QHeaderView::section {{
        background: {_rgba(BG, 200)}; color: {ORANGE.name()};
        border: none; padding: 3px 6px; font-size: {font_pt - 1}pt;
    }}
    QScrollBar:vertical {{ background: transparent; width: 8px; }}
    QScrollBar::handle:vertical {{ background: {_rgba(ORANGE, 120)}; border-radius: 4px; }}
    QScrollBar::add-line, QScrollBar::sub-line {{ height: 0; }}
    """
