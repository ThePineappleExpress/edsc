import re
from pathlib import Path
from unittest import mock

import pytest
from PySide6.QtCore import Qt
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
    assert "QLineEdit, QSpinBox, QComboBox" in css
    assert "QSlider::handle:horizontal" in css
    assert f"QLabel#{theme.MUTED_ROLE}" in css
    assert f"QLabel#{theme.ERROR_ROLE}" in css
    assert "font-size: 11pt" in css


@pytest.mark.parametrize(
    ("font_pt", "expected"),
    [(7, 1), (10, 2), (15, 3), (20, 4)],
)
def test_text_padding_scales_linearly_from_two_pixels_at_ten_points(
    font_pt, expected
):
    assert theme.text_padding_px(font_pt) == expected


def test_every_text_surface_receives_scaled_padding():
    app_css = theme.application_stylesheet(font_pt=10)
    panel_css = theme.panel_stylesheet(alpha=200, font_pt=10)

    assert (
        f"QDialog QLabel {{ color: {theme.ORANGE.name()}; padding: 2px; }}"
        in app_css
    )
    assert "QCheckBox, QRadioButton {\n        color:" in app_css
    assert "padding: 2px; spacing: 6px" in app_css
    assert "QLabel {\n        color:" in panel_css
    assert "padding: 2px;" in panel_css
    assert "QTableView::item { padding: 2px; }" in panel_css
    assert f"QLabel#{theme.TITLE_ROLE} {{" in panel_css
    assert "padding: 2px; font-weight: 900" in panel_css
    assert f"QLabel#{theme.STATUS_ROLE}, QLabel#{theme.CREDIT_ROLE}" in panel_css


@pytest.mark.parametrize(("font_pt", "padding"), [(10, 2), (20, 4)])
def test_label_roles_use_global_padding_not_their_internal_font_size(
    font_pt, padding
):
    css = theme.panel_stylesheet(alpha=200, font_pt=font_pt)

    title_rule = css.split(f"QLabel#{theme.TITLE_ROLE} {{", 1)[1].split("}", 1)[0]
    status_rule = css.split(f"QLabel#{theme.STATUS_ROLE},", 1)[1].split("}", 1)[0]
    assert f"font-size: {font_pt + 4}pt" in title_rule
    assert f"padding: {padding}px" in title_rule
    assert f"font-size: {font_pt - 2}pt" in status_rule
    assert f"padding: {padding}px" in status_rule


def test_component_padding_grows_with_the_configured_font():
    app_css = theme.application_stylesheet(font_pt=20)
    panel_css = theme.panel_stylesheet(alpha=200, font_pt=20)

    assert "padding: 6px 12px;" in app_css  # line edit / spin box
    assert "padding: 8px 24px;" in app_css  # dialog tabs
    assert "padding: 6px 20px;" in panel_css  # overlay tabs and tools
    assert "QTableView::item { padding: 4px; }" in panel_css


def test_overlay_uses_orange_text_transparent_headers_and_tab_style_buttons():
    css = theme.panel_stylesheet(alpha=200, font_pt=10)
    assert f"color: {theme.ORANGE.name()}; font-size: 10pt" in css
    assert f"QTableView {{\n        color: {theme.ORANGE.name()}" in css
    assert (
        "QHeaderView {\n"
        "        background: transparent;\n"
        "    }\n"
        "    QHeaderView::section {\n"
        "        background: transparent; "
        f"color: {theme.ORANGE_DIM.name()}"
    ) in css
    assert "QToolButton:checked, QToolButton:pressed" in css
    assert f"color: {theme.BG_TAB.name()}; background: rgba(" in css


def test_configure_table_left_aligns_column_headers():
    table = mock.Mock()

    theme.configure_table(table)

    table.horizontalHeader().setDefaultAlignment.assert_called_once_with(
        Qt.AlignLeft | Qt.AlignVCenter
    )


def test_table_rows_scale_with_the_font_size():
    table = mock.Mock()

    theme.configure_table(table, font_pt=20)

    table.verticalHeader().setDefaultSectionSize.assert_called_once_with(40)


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
