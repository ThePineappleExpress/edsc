import re
from pathlib import Path

import pytest
from PySide6.QtGui import QPalette

from edsc.gui import theme


@pytest.fixture(autouse=True)
def restore_stock_palette():
    yield
    theme.apply_hud_matrix(None)


def test_stock_palette_in_stylesheet():
    css = theme.panel_stylesheet(alpha=200, font_pt=10)
    assert "#ff8214" in css                 # stock HUD orange
    assert "rgba(0,0,0,200)" in css         # panel background at given alpha


def test_application_palette_comes_from_theme_colours():
    palette = theme.application_palette()
    assert palette.color(QPalette.Window) == theme.BG
    assert palette.color(QPalette.WindowText) == theme.ORANGE
    assert palette.color(QPalette.Text) == theme.ORANGE
    assert palette.color(QPalette.ButtonText) == theme.ORANGE_DIM
    assert palette.color(QPalette.Highlight) == theme.ORANGE
    assert palette.color(QPalette.PlaceholderText) == theme.TEXT_DIM


def test_application_stylesheet_covers_native_widgets():
    css = theme.application_stylesheet(font_pt=11)
    assert "QDialog, QMenu" in css
    assert "QLineEdit, QSpinBox" in css
    assert "QSlider::handle:horizontal" in css
    assert f"QLabel#{theme.MUTED_ROLE}" in css
    assert "font-size: 11pt" in css


def test_overlay_uses_orange_text_dim_headers_and_tab_style_buttons():
    css = theme.panel_stylesheet(alpha=200, font_pt=10)
    assert f"QLabel {{ color: {theme.ORANGE.name()}" in css
    assert f"QTableView {{\n        color: {theme.ORANGE.name()}" in css
    assert (
        f"QHeaderView::section {{\n"
        f"        background: rgba({theme.BG.red()},{theme.BG.green()},"
        f"{theme.BG.blue()},200); color: {theme.ORANGE_DIM.name()}"
    ) in css
    assert "QToolButton:checked, QToolButton:pressed" in css
    assert f"color: {theme.BG_TAB.name()}; background: rgba(" in css


def test_window_controls_have_a_dedicated_compact_style():
    css = theme.panel_stylesheet(alpha=200, font_pt=10)
    selector = f"QToolButton#{theme.WINDOW_CONTROL_ROLE}"
    assert theme.METRICS.window_control_size == (24, 22)
    assert f"{selector} {{" in css
    assert f"{selector}:hover" in css
    assert f"{selector}:checked" in css
    assert "padding: 0" in css


def test_matrix_shifts_palette_and_stylesheet():
    # Swap red and green: the orange HUD turns green.
    theme.apply_hud_matrix(((0, 1, 0), (1, 0, 0), (0, 0, 1)))
    assert (theme.ORANGE.red(), theme.ORANGE.green(), theme.ORANGE.blue()) == (
        130,
        255,
        20,
    )
    assert "#82ff14" in theme.panel_stylesheet(alpha=200, font_pt=10)
    assert "#82ff14" in theme.application_stylesheet(font_pt=10)


def test_status_colours_ignore_the_matrix():
    # A red<->green swap must not turn "short" green and "done" red.
    theme.apply_hud_matrix(((0, 1, 0), (1, 0, 0), (0, 0, 1)))
    assert (theme.DONE.red(), theme.DONE.green(), theme.DONE.blue()) == (64, 224, 96)
    assert (theme.READY.red(), theme.READY.green(), theme.READY.blue()) == (
        255,
        208,
        32,
    )
    assert (theme.SHORT.red(), theme.SHORT.green(), theme.SHORT.blue()) == (
        255,
        72,
        64,
    )


def test_none_restores_stock_palette():
    theme.apply_hud_matrix(((0, 1, 0), (1, 0, 0), (0, 0, 1)))
    theme.apply_hud_matrix(None)
    assert (theme.ORANGE.red(), theme.ORANGE.green(), theme.ORANGE.blue()) == (
        255,
        130,
        20,
    )


def test_visual_rules_are_centralized_in_theme_module():
    gui_dir = Path(theme.__file__).parent
    qss_declaration = re.compile(
        r"\b(?:background|border|color|font-size|font-weight|margin|padding)"
        r"(?:-[a-z]+)?\s*:"
    )
    hex_colour = re.compile(r"#[0-9a-fA-F]{6}\b")

    for path in gui_dir.glob("*.py"):
        if path.name == "theme.py":
            continue
        source = path.read_text(encoding="utf-8")
        assert not hex_colour.search(source), f"hard-coded colour in {path.name}"
        assert not qss_declaration.search(source), f"QSS outside theme.py in {path.name}"
        assert "setObjectName(" not in source, f"raw style role in {path.name}"
        assert "QFont(" not in source, f"font construction outside theme.py in {path.name}"
