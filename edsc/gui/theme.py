"""Shared colours and style for the overlay -- Elite Dangerous HUD palette."""

from __future__ import annotations

from PySide6.QtGui import QColor

ORANGE = QColor(255, 130, 20)
ORANGE_DIM = QColor(180, 92, 14)
TEXT = QColor(235, 232, 226)
TEXT_DIM = QColor(150, 146, 138)
DONE = QColor(96, 176, 108)        # delivered in full
READY = QColor(240, 196, 60)       # carrying enough to finish this line
SHORT = QColor(226, 108, 92)       # still need to acquire more
PANEL_BG = "rgba(12, 14, 18, {a})"  # {a} filled with 0..255 alpha
GRID = QColor(60, 54, 44)


def panel_stylesheet(alpha: int, font_pt: int) -> str:
    """Stylesheet for the overlay's inner panel at a given background alpha."""
    bg = PANEL_BG.format(a=alpha)
    return f"""
    QFrame#panel {{
        background-color: {bg};
        border: 1px solid rgba(255,130,20,90);
        border-radius: 10px;
    }}
    QLabel {{ color: #ebe8e2; font-size: {font_pt}pt; }}
    QLabel#title {{ color: #ff8214; font-size: {font_pt + 2}pt; font-weight: 600; }}
    QLabel#subtitle {{ color: #96928a; font-size: {font_pt - 1}pt; }}
    QLabel#status {{ color: #96928a; font-size: {font_pt - 2}pt; }}
    QLabel#credit {{ color: #b45c0e; font-size: {font_pt - 2}pt; }}
    QTabBar {{ qproperty-drawBase: 0; }}
    QTabBar::tab {{
        color: #96928a; background: rgba(30,26,20,180);
        border: 1px solid rgba(255,130,20,60);
        border-top-left-radius: 4px; border-top-right-radius: 4px;
        padding: 3px 10px; margin-right: 2px; font-size: {font_pt - 1}pt;
        max-width: 140px;
    }}
    QTabBar::tab:selected {{
        color: #ff8214; background: rgba(90,54,14,220);
        border-color: #ff8214;
    }}
    QTabBar::tab:hover {{ color: #ebe8e2; }}
    QTabBar QToolButton {{
        background: rgba(40,34,26,220); color: #ff8214;
        border: 1px solid rgba(255,130,20,90);
    }}
    QToolButton {{
        color: #ebe8e2; background: rgba(40,34,26,180);
        border: 1px solid rgba(255,130,20,90); border-radius: 4px;
        padding: 2px 6px; font-size: {font_pt - 1}pt;
    }}
    QToolButton:hover {{ background: rgba(90,54,14,220); }}
    QToolButton:checked {{ background: #6b3a08; border-color: #ff8214; }}
    QProgressBar {{
        background: rgba(40,34,26,160); border: none; border-radius: 4px;
        height: 6px; text-align: center; color: transparent;
    }}
    QProgressBar::chunk {{ background-color: #ff8214; border-radius: 4px; }}
    QTableView {{
        background: transparent; color: #ebe8e2; gridline-color: #3c362c;
        font-size: {font_pt}pt; selection-background-color: transparent;
        outline: none;
        /* Faint warm lightening of every other row; just enough to follow a
           line across a busy list without shouting. */
        alternate-background-color: rgba(255, 210, 160, 14);
    }}
    QHeaderView::section {{
        background: rgba(40,34,26,200); color: #ff8214;
        border: none; padding: 3px 6px; font-size: {font_pt - 1}pt;
    }}
    QScrollBar:vertical {{ background: transparent; width: 8px; }}
    QScrollBar::handle:vertical {{ background: rgba(255,130,20,120); border-radius: 4px; }}
    QScrollBar::add-line, QScrollBar::sub-line {{ height: 0; }}
    """
