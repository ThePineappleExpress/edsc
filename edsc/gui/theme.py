"""Shared colors, metrics, fonts, and styles for the Qt GUI."""

# SPDX-License-Identifier: GPL-3.0-or-later

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
    collapsed_icon_px: int = 128
    about_icon_px: int = 256
    collapse_content_fade_ms: int = 110
    collapse_height_ms: int = 130
    collapse_width_ms: int = 150
    collapse_icon_fade_ms: int = 100
    collapse_shell_fade_ms: int = 90
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
    settings_dialog_minimum_width: int = 660
    carrier_dialog_minimum_width: int = 360
    opacity_percent_minimum: int = 0
    opacity_percent_maximum: int = 100
    font_point_minimum: int = 7
    font_point_maximum: int = 20


METRICS = ThemeMetrics()

# Object names used as stylesheet roles; keeping the selectors here spares widgets from knowing how a role is represented in QSS.
PANEL_ROLE = "panel"
TITLE_ROLE = "title"
SUBTITLE_ROLE = "subtitle"
STATUS_ROLE = "status"
CREDIT_ROLE = "credit"
MUTED_ROLE = "muted"
ERROR_ROLE = "error"
COMPLETE_BUTTON_ROLE = "completeButton"
WINDOW_CONTROL_ROLE = "windowControl"
IMAGE_ROLE = "image"
# About / Help decoration.
TIP_CARD_ROLE = "tipCard"
TIP_GLYPH_ROLE = "tipGlyph"
TIP_TITLE_ROLE = "tipTitle"

_PADDING_REFERENCE_PT = 10
_TEXT_PADDING_AT_REFERENCE_PX = 2

# Stock (identity-matrix) palette; the module globals below are re-derived from these whenever a HUD colour matrix is applied.
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

# Traffic-light status colours keep their stock values regardless of the HUD matrix: they encode delivered/ready/short, and a matrix tuned for the game's orange chrome would crush them to near-white -- or, for a red<->green swap, invert their meaning.
_SEMANTIC = frozenset({"DONE", "READY", "SHORT", "DONE_BG", "DONE_BG_HOVER"})

ORANGE = QColor(*_BASE["ORANGE"])
ORANGE_DIM = QColor(*_BASE["ORANGE_DIM"])
TEXT = QColor(*_BASE["TEXT"])
TEXT_DIM = QColor(*_BASE["TEXT_DIM"])
DONE = QColor(*_BASE["DONE"])
READY = QColor(*_BASE["READY"])
SHORT = QColor(*_BASE["SHORT"])
GRID = QColor(*_BASE["GRID"])
PANEL_BG = QColor(*_BASE["PANEL_BG"])
BG = QColor(*_BASE["BG"])
BG_TAB = QColor(*_BASE["BG_TAB"])
BG_ACTIVE = QColor(*_BASE["BG_ACTIVE"])
BG_CHECKED = QColor(*_BASE["BG_CHECKED"])
DONE_BG = QColor(*_BASE["DONE_BG"])
DONE_BG_HOVER = QColor(*_BASE["DONE_BG_HOVER"])
ALT_ROW = QColor(*_BASE["ALT_ROW"])

_current_hud_matrix: Matrix | None = None


def apply_hud_matrix(matrix: Matrix | None) -> None:
    """Re-derive the palette through the game's HUD colour matrix; ``None`` (or the identity) restores the stock orange. Stylesheets and the application palette need rebuilding afterwards, while colours read per-paint (e.g. table models) pick it up on their own."""
    global _current_hud_matrix
    _current_hud_matrix = matrix
    for name, rgb in _BASE.items():
        if matrix is not None and name not in _SEMANTIC:
            rgb = transform(matrix, rgb)
        globals()[name] = QColor(*rgb)


def current_hud_matrix() -> Matrix | None:
    """Matrix behind the current palette, for selectively recoloured art."""
    return _current_hud_matrix


def adjust_hud_rgb(rgb: tuple[int, int, int]) -> tuple[int, int, int]:
    """Transform one RGB colour through the current HUD matrix, if any."""
    if _current_hud_matrix is None:
        return rgb
    return transform(_current_hud_matrix, rgb)


apply_hud_matrix(None)


def _rgba(c: QColor, alpha: int) -> str:
    return f"rgba({c.red()},{c.green()},{c.blue()},{alpha})"


def set_role(widget: QWidget, role: str) -> None:
    """Assign a theme-owned stylesheet role to ``widget``."""
    widget.setObjectName(role)


def scaled_px(value_at_10pt: int, font_pt: int) -> int:
    """Scale a pixel metric linearly from its value at a 10-point font."""
    value = max(0, int(value_at_10pt))
    point_size = max(1, int(font_pt))
    return (value * point_size + _PADDING_REFERENCE_PT // 2) // _PADDING_REFERENCE_PT


def text_padding_px(configured_font_pt: int) -> int:
    """Global text inset: two pixels at the 10-point Settings value; widget roles may render larger or smaller than the base font but all keep this one globally derived inset."""
    return max(1, scaled_px(_TEXT_PADDING_AT_REFERENCE_PX, configured_font_pt))


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


def configure_table(
    table: QTableView, *, elide_text: bool = False, font_pt: int = 10
) -> None:
    """Apply the shared visual presentation used by overlay tables."""
    table.setShowGrid(False)
    table.setAlternatingRowColors(True)
    update_table_metrics(table, font_pt)
    table.horizontalHeader().setDefaultAlignment(Qt.AlignLeft | Qt.AlignVCenter)
    table.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
    if elide_text:
        table.setWordWrap(False)
        table.setTextElideMode(Qt.ElideRight)


def update_table_metrics(table: QTableView, font_pt: int) -> None:
    """Resize table rows when the configured font point size changes."""
    table.verticalHeader().setDefaultSectionSize(
        scaled_px(METRICS.table_row_height, font_pt)
    )


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
    text_pad = text_padding_px(font_pt)

    def px(value_at_10pt: int) -> int:
        return scaled_px(value_at_10pt, font_pt)

    return f"""
    QDialog, QMenu {{
        background-color: {BG.name()}; color: {ORANGE.name()};
        font-size: {font_pt}pt;
    }}
    QDialog QLabel {{ color: {ORANGE.name()}; padding: {text_pad}px; }}
    QLabel#{MUTED_ROLE} {{ color: {ORANGE_DIM.name()}; }}
    QLabel#{ERROR_ROLE} {{ color: {SHORT.name()}; }}
    QLineEdit, QSpinBox, QComboBox {{
        background-color: {PANEL_BG.name()}; color: {ORANGE.name()};
        padding: {px(3)}px {px(6)}px;
        selection-background-color: {ORANGE.name()};
        selection-color: {BG.name()};
    }}
    QLineEdit:focus, QSpinBox:focus, QComboBox:focus {{ border-color: {ORANGE.name()}; }}
    QPushButton {{
        color: {BG_TAB.name()}; background-color: {_rgba(BG, 180)};
        border: none; padding: {px(3)}px {px(10)}px; font-weight: 600;
    }}
    QPushButton:hover {{
        color: {TEXT.name()}; background-color: {_rgba(ORANGE_DIM, 220)};
    }}
    QPushButton:pressed, QPushButton:default {{
        color: {BG.name()}; background-color: {_rgba(ORANGE, 220)};
    }}
    QCheckBox, QRadioButton {{
        color: {ORANGE.name()}; padding: {text_pad}px; spacing: {px(6)}px;
    }}
    QTabWidget::pane {{
        border: 1px solid {GRID.name()}; top: -1px;
    }}
    QTabBar {{ qproperty-drawBase: 0; }}
    QTabBar::tab {{
        color: {BG_TAB.name()}; background: {_rgba(BG, 180)};
        padding: {px(4)}px {px(12)}px;
        margin-right: {px(2)}px; font-weight: 600;
    }}
    QTabBar::tab:selected {{
        color: {BG.name()}; background: {_rgba(ORANGE, 220)};
    }}
    QTabBar::tab:hover:!selected {{
        color: {TEXT.name()}; background: {_rgba(ORANGE_DIM, 220)};
    }}
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
    QMenu {{ border: 1px solid {GRID.name()}; padding: {px(3)}px; }}
    QMenu::item {{
        padding: {px(4)}px {px(22)}px {px(4)}px {px(8)}px;
    }}
    QMenu::item:selected {{
        background-color: {ORANGE.name()}; color: {BG.name()};
    }}
    QToolTip {{
        background-color: {BG.name()}; color: {ORANGE.name()};
        padding: {px(10)}px; border: none;
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
    corner_icon: str = "",
) -> str:
    """Tooltip identity block: market freshness in fine print on top, then the type sprite beside the bold station name (with the proprietor line -- controlling minor faction -- two points smaller beneath it when given) and the system/station-kind lines; inputs are already-escaped rich text, ``icon`` is an inline ``<img>``, and ``corner_icon`` (when supplied) anchors top-right spanning both identity rows."""
    base = tooltip_base_pt()
    title_pt, sub_pt, fine_pt = base + 2, base - 1, base - 2
    sub = f"color: {ORANGE.name()}; font-size: {sub_pt}pt"
    owner_line = (
        f"<br><span style='color: {ORANGE.name()}; "
        f"font-size: {title_pt - 2}pt'>{owner}</span>"
        if owner
        else ""
    )
    corner = (
        f"<td rowspan='2' valign='top' align='right' "
        f"style='padding-left: 12px'>{corner_icon}</td>"
        if corner_icon
        else ""
    )
    return (
        "<table width='100%' cellspacing='0' cellpadding='0'><tr>"
        f"<td colspan='2'><span style='font-size: {fine_pt}pt'>"
        f"{freshness}</span></td>{corner}</tr><tr>"
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
    """Stocked/missing commodity lists as a borderless rich-text table; stocked entries are (escaped name, stocked-percent, fully-stocked) triples with the percent right-aligned (green when fully stocked, else yellow), missing entries are plain escaped names in red, and the Missing side is omitted entirely when nothing is missing."""
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


def tooltip_note(text: str, colour: QColor) -> str:
    """One coloured rich-text tooltip line; ``text`` is already-escaped."""
    return f"<span style='color: {colour.name()}'>{text}</span>"


def tooltip_pair_table(
    left_heading: str,
    left: list[str],
    right_heading: str,
    right: list[str],
) -> str:
    """Two parallel lists as a borderless rich-text table with dim headings; entries are already-escaped rich text in HUD orange, and the right column is omitted entirely when it has no entries."""
    dim = ORANGE_DIM.name()
    orange = ORANGE.name()
    gap = "padding-left: 16px; "
    headings = f"<th align='left' style='color: {dim}'>{left_heading}</th>"
    if right:
        headings += (
            f"<th align='left' style='{gap}color: {dim}'>{right_heading}</th>"
        )
    rows = []
    for i in range(max(len(left), len(right))):
        cells = (
            f"<td style='color: {orange}'>{left[i] if i < len(left) else ''}</td>"
        )
        if right:
            entry = right[i] if i < len(right) else ""
            cells += f"<td style='{gap}color: {orange}'>{entry}</td>"
        rows.append(f"<tr>{cells}</tr>")
    return (
        "<table cellspacing='0' cellpadding='1'>"
        f"<tr>{headings}</tr>{''.join(rows)}</table>"
    )


def settings_panel_stylesheet(alpha: int, font_pt: int) -> str:
    """Panel style for the settings dialog: the overlay panel with every inner frame outline removed so the whole form reads as one borderless HUD surface -- the single source of truth for the dialog's chrome."""
    return panel_stylesheet(alpha, font_pt) + f"""
    QTabWidget::pane {{ border: 0; top: 0; }}
    QScrollArea {{ border: 0; background: transparent; }}
    QFrame#{TIP_CARD_ROLE} {{
        background: {_rgba(BG, 120)};
        border-left: 2px solid {ORANGE_DIM.name()};
    }}
    QLabel#{TIP_GLYPH_ROLE} {{
        color: {ORANGE.name()}; font-size: {font_pt + 9}pt; font-weight: 900;
    }}
    QLabel#{TIP_TITLE_ROLE} {{
        color: {ORANGE.name()}; font-size: {font_pt}pt; font-weight: 800;
    }}
    """


def about_homage_stylesheet() -> str:
    """Colour overrides pinning the About tab to stock Elite orange; every other surface follows the player's HUD matrix but About deliberately doesn't (a homage to the original UI). Scoped to the About page's own stylesheet (so they win over the panel's matrix-derived colours for that subtree only) and touching colour alone (font/padding still inherit from the panel sheet), they read the stock palette straight from ``_BASE`` so a custom HUD never reaches them."""
    orange = QColor(*_BASE["ORANGE"]).name()
    orange_dim = QColor(*_BASE["ORANGE_DIM"]).name()
    return f"""
    QLabel {{ color: {orange}; }}
    QLabel#{TITLE_ROLE} {{ color: {orange_dim}; }}
    QLabel#{SUBTITLE_ROLE} {{ color: {orange}; }}
    QLabel#{MUTED_ROLE} {{ color: {orange_dim}; }}
    QLabel#{STATUS_ROLE}, QLabel#{CREDIT_ROLE} {{ color: {orange_dim}; }}
    """


def about_homage_chrome_stylesheet() -> str:
    """Stock colours for chrome shown while the About page is active; the selected tab and OK/Cancel buttons live outside the About page, so its subtree stylesheet can't reach them -- this companion sheet applies directly to those two widgets only while About is selected, covering interaction states too so hover/press/default-OK don't fall back to the player's HUD matrix."""
    orange = QColor(*_BASE["ORANGE"])
    orange_dim = QColor(*_BASE["ORANGE_DIM"])
    text = QColor(*_BASE["TEXT"])
    bg = QColor(*_BASE["BG"])
    bg_tab = QColor(*_BASE["BG_TAB"])
    return f"""
    QTabBar::tab:selected,
    QTabBar::tab:selected:hover {{
        color: {bg.name()}; background: {_rgba(orange, 220)};
    }}
    QPushButton {{
        color: {bg_tab.name()}; background-color: {_rgba(bg, 180)};
    }}
    QPushButton:hover {{
        color: {text.name()}; background-color: {_rgba(orange_dim, 220)};
    }}
    QPushButton:pressed, QPushButton:default {{
        color: {bg.name()}; background-color: {_rgba(orange, 220)};
    }}
    """


def anti_xeno_briefing_stylesheet() -> str:
    """Style the readable briefing shown by the hidden Anti-Xeno sequence."""
    return f"""
    QPlainTextEdit {{
        background-color: {_rgba(BG, 210)};
        color: {TEXT.name()};
        border: 1px solid {ORANGE_DIM.name()};
        padding: 8px;
    }}
    QScrollBar:vertical {{
        background: {BG.name()};
        width: 10px;
    }}
    QScrollBar::handle:vertical {{
        background: {ORANGE_DIM.name()};
        min-height: 24px;
    }}
    """


def panel_stylesheet(alpha: int, font_pt: int) -> str:
    """Stylesheet for the overlay's inner panel at a given background alpha."""
    text_pad = text_padding_px(font_pt)

    def px(value_at_10pt: int) -> int:
        return scaled_px(value_at_10pt, font_pt)

    return f"""
    QFrame#{PANEL_ROLE} {{
        background-color: {_rgba(PANEL_BG, alpha)};
    }}
    QLabel {{
        color: {ORANGE.name()}; font-size: {font_pt}pt;
        padding: {text_pad}px;
    }}
    QLabel#{TITLE_ROLE} {{
        color: {ORANGE_DIM.name()}; font-size: {font_pt + 4}pt;
        padding: {text_pad}px; font-weight: 900;
    }}
    QLabel#{SUBTITLE_ROLE} {{
        color: {ORANGE.name()}; font-size: {font_pt}pt;
        padding: {text_pad}px;
    }}
    QLabel#{STATUS_ROLE}, QLabel#{CREDIT_ROLE} {{
        color: {ORANGE_DIM.name()}; font-size: {font_pt - 2}pt;
        padding: {text_pad}px;
    }}
    QLabel#{ERROR_ROLE} {{ color: {SHORT.name()}; }}
    QLabel#{IMAGE_ROLE} {{ padding: 0; }}
    QTabBar {{ qproperty-drawBase: 0; }}
    QTabBar::tab {{
        color: {BG_TAB.name()}; background: {_rgba(BG, 180)};
        padding: {px(3)}px {px(10)}px; margin-right: {px(2)}px;
        font-size: {font_pt}pt; font-weight: 600;
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
        border: none; padding: {px(3)}px {px(10)}px;
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
        padding: {px(4)}px {px(8)}px; font-weight: 600;
    }}
    QToolButton#{COMPLETE_BUTTON_ROLE}:hover {{ background: {_rgba(DONE_BG_HOVER, 220)}; }}
    QProgressBar {{
        background: {_rgba(BG, 160)}; border: none;
        height: 6px; text-align: center; color: transparent;
    }}
    QProgressBar::chunk {{ background-color: {ORANGE_DIM.name()}; }}
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
    QTableView {{
        color: {ORANGE.name()}; background-color: {_rgba(BG, 20)}; gridline-color: {GRID.name()};
        font-size: {font_pt}pt; selection-background-color: transparent;
        outline: none;
        /* Faint warm lightening of every other row; just enough to follow a
           line across a busy list without shouting. */
        alternate-background-color: {_rgba(ORANGE_DIM, 14)};
    }}
    QTableView::item {{ padding: {text_pad}px; }}
    QHeaderView {{
        background: transparent;
    }}
    QHeaderView::section {{
        background: transparent; color: {ORANGE_DIM.name()};
        border: none; padding: {px(3)}px {px(6)}px;
        font-size: {font_pt - 1}pt;
    }}
    QScrollBar:vertical {{ background: transparent; width: 8px; }}
    QScrollBar::handle:vertical {{ background: {_rgba(ORANGE_DIM, 120)}; }}
    QScrollBar::add-line, QScrollBar::sub-line {{ height: 0; }}
    """
