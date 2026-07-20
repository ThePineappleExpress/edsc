
import pytest
from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QImage
from PySide6.QtWidgets import QApplication

from edsc import hud_colors
from edsc.gui import icons, theme
from edsc.paths import asset_path
from edsc.stations import StationResult

_app = QApplication.instance() or QApplication([])


@pytest.fixture(autouse=True)
def restore_stock_hud_colours():
    theme.apply_hud_matrix(None)
    yield
    theme.apply_hud_matrix(None)


def _station(**overrides):
    kwargs = {
        "name": "A", "system": "B", "distance_ly": 0.0, "arrival_ls": 0.0,
        "has_large_pad": True, "is_planetary": False,
        "station_type": "Coriolis Starport", "is_carrier": False,
        "market_updated_at": "",
    }
    kwargs.update(overrides)
    return StationResult(**kwargs)


def test_sprite_key_matches_station_kind():
    def key(station_type, carrier=False, planetary=False):
        return icons.sprite_key(
            station_type, is_carrier=carrier, is_planetary=planetary
        )

    assert key("Coriolis Starport") == "coriolis"
    assert key("Orbis Starport") == "orbis"
    assert key("Ocellus Starport") == "ocellus"
    assert key("Asteroid base") == "asteroid"
    assert key("Drake-Class Carrier", carrier=True) == "carrier"
    assert key("Planetary Outpost", planetary=True) == "planetary"
    # Orbital types without their own art fall back to the dodec sprite.
    assert key("Outpost") == "dodec"
    assert key("Mega ship") == "dodec"
    assert key("") == "dodec"


def test_station_icon_is_tinted_to_the_hud_orange():
    image = icons.station_icon(_station()).pixmap(32, 32).toImage()
    colours = {
        image.pixelColor(x, y).name()
        for x in range(image.width())
        for y in range(image.height())
        if image.pixelColor(x, y).alpha() == 255
    }
    assert colours == {theme.ORANGE.name()}


def test_colonization_icon_uses_the_eighth_sprite_and_hud_tint():
    assert icons._CELLS["colonization"] == (2, 1)

    image = icons.colonization_icon(32).toImage()
    # The colonization glyph has soft (anti-aliased) edges, so no pixel is fully opaque; check the substantially-opaque body and compare hue not the literal rgba (smooth scaling nudges a few channels a step off the HUD orange).
    hue, sat, _, _ = theme.ORANGE.getHsv()
    body = [
        image.pixelColor(x, y)
        for x in range(image.width())
        for y in range(image.height())
        if image.pixelColor(x, y).alpha() >= 128
    ]
    assert body
    assert all(
        abs(pixel.hsvHue() - hue) <= 2 and pixel.hsvSaturation() >= sat - 8
        for pixel in body
    )


def test_powered_logo_fits_the_box_and_is_cached():
    pixmap = icons.powered_logo_pixmap("eddn", 104, 44)
    # Aspect-preserving fit inside the box (the EDDN wordmark is width-bound).
    assert pixmap.width() <= 104 and pixmap.height() <= 44
    assert icons.powered_logo_pixmap("eddn", 104, 44) is pixmap


def test_powered_logo_keeps_the_brand_palette():
    # The marks render in their own colours, so a HUD recolour leaves them untouched and the art isn't flattened to the HUD accent hue.
    stock = icons.powered_logo_pixmap("eddn", 64, 64)
    theme.apply_hud_matrix(((0.0, 1.0, 0.0), (1.0, 0.0, 0.0), (0.0, 0.0, 0.5)))
    assert icons.powered_logo_pixmap("eddn", 64, 64) is stock

    image = stock.toImage()
    hues = [
        image.pixelColor(x, y).hsvHue()
        for x in range(image.width())
        for y in range(image.height())
        if image.pixelColor(x, y).alpha() >= 128
        and image.pixelColor(x, y).hsvSaturation() >= 40
    ]
    # EDDN's wordmark is green (hue ~120); a HUD tint would drag every pixel to the orange accent (~28), so the retained green proves the brand palette.
    assert hues
    assert all(90 <= hue <= 150 for hue in hues)


def test_powered_logo_html_embeds_the_fitted_asset():
    html = icons.powered_logo_html(
        "raven", 32, 32, alt="Confirmed by Raven Colonial"
    )
    assert html.startswith("<img src='data:image/png;base64,")
    assert "width='32' height='32'" in html
    assert "alt='Confirmed by Raven Colonial'" in html


def test_station_icon_html_embeds_a_sized_inline_image():
    html = icons.station_icon_html(_station(), 24)
    assert html.startswith("<img src='data:image/png;base64,")
    assert "width='24' height='24'" in html


def test_app_glyph_accent_rule_targets_orange_only():
    assert icons._is_app_glyph_accent(QColor(230, 120, 20))
    assert not icons._is_app_glyph_accent(QColor(120, 120, 120))
    assert not icons._is_app_glyph_accent(QColor(20, 120, 230))


def _app_glyph_source(px):
    return QImage(str(asset_path("icon.png"))).scaled(
        px, px, Qt.KeepAspectRatio, Qt.SmoothTransformation
    )


def _opaque_pixel(image, predicate):
    return next(
        (x, y)
        for y in range(image.height())
        for x in range(image.width())
        if image.pixelColor(x, y).alpha() == 255
        and predicate(image.pixelColor(x, y))
    )


def test_app_glyph_keeps_its_original_colours_with_the_stock_hud():
    pixmap = icons.app_glyph_pixmap(128)

    assert (pixmap.width(), pixmap.height()) == (128, 128)
    assert icons.app_glyph_pixmap(128) is pixmap

    image = pixmap.toImage()
    source = _app_glyph_source(128)
    assert image.pixelColor(0, 0).alpha() == 0
    for predicate in (
        lambda colour: colour.hsvSaturation() > 200,
        lambda colour: 40 < colour.red() < 220
        and colour.red() == colour.green() == colour.blue(),
        lambda colour: colour.red() == colour.green() == colour.blue() == 255,
    ):
        x, y = _opaque_pixel(source, predicate)
        assert image.pixelColor(x, y) == source.pixelColor(x, y)


def test_app_glyph_stock_stays_original_orange_under_a_hud_matrix():
    # A HUD matrix that visibly shifts orange; the stock emblem must ignore it.
    theme.apply_hud_matrix(((0.0, 1.0, 0.0), (1.0, 0.0, 0.0), (0.0, 0.0, 0.5)))
    source = _app_glyph_source(128)
    orange_xy = _opaque_pixel(source, lambda colour: colour.hsvSaturation() > 200)

    stock = icons.app_glyph_pixmap(128, stock=True).toImage()
    matrixed = icons.app_glyph_pixmap(128).toImage()

    assert icons.app_glyph_pixmap(128, stock=True) is icons.app_glyph_pixmap(
        128, stock=True
    )
    # Stock copies the source accent verbatim; the themed emblem shifts it.
    assert stock.pixelColor(*orange_xy) == source.pixelColor(*orange_xy)
    assert matrixed.pixelColor(*orange_xy) != source.pixelColor(*orange_xy)


def test_app_glyph_adjusts_orange_but_preserves_grayscale_elements():
    # This matrix visibly alters neutral colours if applied to them, making the assertion stronger than a simple channel swap (which leaves gray equal).
    matrix = ((0.0, 1.0, 0.0), (1.0, 0.0, 0.0), (0.0, 0.0, 0.5))
    source = _app_glyph_source(128)
    stock = icons.app_glyph_pixmap(128)
    theme.apply_hud_matrix(matrix)

    tinted = icons.app_glyph_pixmap(128)
    image = tinted.toImage()

    assert tinted is not stock
    assert icons.app_glyph_pixmap(128) is tinted

    orange_xy = _opaque_pixel(source, lambda colour: colour.hsvSaturation() > 200)
    gray_xy = _opaque_pixel(
        source,
        lambda colour: 40 < colour.red() < 220
        and colour.red() == colour.green() == colour.blue(),
    )
    white_xy = _opaque_pixel(
        source,
        lambda colour: colour.red() == colour.green() == colour.blue() == 255,
    )

    orange = source.pixelColor(*orange_xy)
    expected_orange = hud_colors.transform(
        matrix, (orange.red(), orange.green(), orange.blue())
    )
    adjusted = image.pixelColor(*orange_xy)
    assert (adjusted.red(), adjusted.green(), adjusted.blue()) == expected_orange
    assert adjusted != orange

    # Mid-gray and white would both be changed by the matrix above; the emblem's selective rule must nevertheless copy them exactly.
    assert image.pixelColor(*gray_xy) == source.pixelColor(*gray_xy)
    assert image.pixelColor(*white_xy) == source.pixelColor(*white_xy)
