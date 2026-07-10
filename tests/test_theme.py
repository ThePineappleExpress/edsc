import pytest

from edsc.gui import theme


@pytest.fixture(autouse=True)
def restore_stock_palette():
    yield
    theme.apply_hud_matrix(None)


def test_stock_palette_in_stylesheet():
    css = theme.panel_stylesheet(alpha=200, font_pt=10)
    assert "#ff8214" in css                 # stock HUD orange
    assert "rgba(12,14,18,200)" in css      # panel background at given alpha


def test_matrix_shifts_palette_and_stylesheet():
    # Swap red and green: the orange HUD turns green.
    theme.apply_hud_matrix(((0, 1, 0), (1, 0, 0), (0, 0, 1)))
    assert (theme.ORANGE.red(), theme.ORANGE.green(), theme.ORANGE.blue()) == (
        130,
        255,
        20,
    )
    assert "#82ff14" in theme.panel_stylesheet(alpha=200, font_pt=10)


def test_status_colours_ignore_the_matrix():
    # A red<->green swap must not turn "short" green and "done" red.
    theme.apply_hud_matrix(((0, 1, 0), (1, 0, 0), (0, 0, 1)))
    assert (theme.DONE.red(), theme.DONE.green(), theme.DONE.blue()) == (96, 176, 108)
    assert (theme.SHORT.red(), theme.SHORT.green(), theme.SHORT.blue()) == (
        226,
        108,
        92,
    )


def test_none_restores_stock_palette():
    theme.apply_hud_matrix(((0, 1, 0), (1, 0, 0), (0, 0, 1)))
    theme.apply_hud_matrix(None)
    assert (theme.ORANGE.red(), theme.ORANGE.green(), theme.ORANGE.blue()) == (
        255,
        130,
        20,
    )
