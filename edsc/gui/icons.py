"""Station-type icons cut from the sprite map and tinted to the HUD colour.


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

from typing import TYPE_CHECKING

from PySide6.QtCore import QBuffer, QByteArray, QIODevice, QRect
from PySide6.QtGui import QIcon, QImage, QPainter, QPixmap

from ..paths import asset_path
from . import theme

if TYPE_CHECKING:
    from ..stations import StationResult

# Cell (row, column) of each sprite in assets/icon_map.png, a 3x3 grid of
# white-on-transparent glyphs; the last two cells are unused.
_CELLS = {
    "asteroid": (0, 0),
    "coriolis": (0, 1),
    "orbis": (0, 2),
    "ocellus": (1, 0),
    "carrier": (1, 1),
    "planetary": (1, 2),
    "dodec": (2, 0),
}
_GRID = 3

_sheet: QImage | None = None
# Caches keyed by (sprite, tint rgba) so a HUD recolour re-derives everything.
_images: dict[tuple[str, int], QImage] = {}
_icons: dict[tuple[str, int], QIcon] = {}
_uris: dict[tuple[str, int], str] = {}


def sprite_key(station_type: str, *, is_carrier: bool, is_planetary: bool) -> str:
    """Sprite for a station; orbital types without their own art get the dodec."""
    if is_carrier:
        return "carrier"
    if is_planetary:
        return "planetary"
    lowered = (station_type or "").lower()
    for key in ("asteroid", "coriolis", "orbis", "ocellus"):
        if key in lowered:
            return key
    return "dodec"


def _cache_key(station: StationResult) -> tuple[str, int]:
    key = sprite_key(
        station.station_type,
        is_carrier=station.is_carrier,
        is_planetary=station.is_planetary,
    )
    return key, theme.ORANGE.rgba()


def _tinted(cache: tuple[str, int]) -> QImage:
    image = _images.get(cache)
    if image is None:
        global _sheet
        if _sheet is None:
            _sheet = QImage(str(asset_path("icon_map.png")))
        cell_w = _sheet.width() // _GRID
        cell_h = _sheet.height() // _GRID
        row, col = _CELLS[cache[0]]
        image = _sheet.copy(QRect(col * cell_w, row * cell_h, cell_w, cell_h))
        image = image.convertToFormat(QImage.Format_ARGB32_Premultiplied)
        # Recolour the white glyph while keeping its alpha shape.
        painter = QPainter(image)
        painter.setCompositionMode(QPainter.CompositionMode_SourceIn)
        painter.fillRect(image.rect(), theme.ORANGE)
        painter.end()
        _images[cache] = image
    return image


def station_icon(station: StationResult) -> QIcon:
    """Tinted type sprite for the table's decoration role."""
    cache = _cache_key(station)
    icon = _icons.get(cache)
    if icon is None:
        icon = QIcon(QPixmap.fromImage(_tinted(cache)))
        _icons[cache] = icon
    return icon


def station_icon_html(station: StationResult, px: int) -> str:
    """Tinted type sprite as an inline ``<img>`` for rich-text tooltips."""
    cache = _cache_key(station)
    uri = _uris.get(cache)
    if uri is None:
        data = QByteArray()
        buffer = QBuffer(data)
        buffer.open(QIODevice.WriteOnly)
        _tinted(cache).save(buffer, "PNG")
        uri = "data:image/png;base64," + bytes(data.toBase64()).decode("ascii")
        _uris[cache] = uri
    return f"<img src='{uri}' width='{px}' height='{px}'>"
