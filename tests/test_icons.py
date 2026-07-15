import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

from edsc.gui import icons, theme
from edsc.stations import StationResult

_app = QApplication.instance() or QApplication([])


def _station(**overrides):
    kwargs = dict(
        name="A", system="B", distance_ly=0.0, arrival_ls=0.0,
        has_large_pad=True, is_planetary=False,
        station_type="Coriolis Starport", is_carrier=False,
        market_updated_at="",
    )
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


def test_station_icon_html_embeds_a_sized_inline_image():
    html = icons.station_icon_html(_station(), 24)
    assert html.startswith("<img src='data:image/png;base64,")
    assert "width='24' height='24'" in html
