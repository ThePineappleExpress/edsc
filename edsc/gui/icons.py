"""Render HUD-tinted station, service, and application icons."""

# SPDX-License-Identifier: GPL-3.0-or-later

from __future__ import annotations

from typing import TYPE_CHECKING

from PySide6.QtCore import QBuffer, QByteArray, QIODevice, QRect, QSize, Qt
from PySide6.QtGui import QColor, QIcon, QImage, QPainter, QPixmap

from ..paths import asset_path
from . import theme

if TYPE_CHECKING:
    from ..stations import StationResult

# Cell (row, column) of each sprite in assets/icon_map.png, a 3x3 grid of white-on-transparent glyphs; the final cell is unused.
_CELLS = {
    "asteroid": (0, 0),
    "coriolis": (0, 1),
    "orbis": (0, 2),
    "ocellus": (1, 0),
    "carrier": (1, 1),
    "planetary": (1, 2),
    "dodec": (2, 0),
    "colonization": (2, 1),
}
_GRID = 3

_sheet: QImage | None = None
# Caches keyed by (sprite, tint rgba) so a HUD recolour re-derives everything.
_images: dict[tuple[str, int], QImage] = {}
_icons: dict[tuple[str, int], QIcon] = {}
_uris: dict[tuple[str, int], str] = {}
_colonization_pixmaps: dict[tuple[int, int], QPixmap] = {}


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


def colonization_icon(px: int) -> QPixmap:
    """HUD-tinted colonization sprite for construction-site UI elements."""
    cache = (px, theme.ORANGE.rgba())
    pixmap = _colonization_pixmaps.get(cache)
    if pixmap is None:
        pixmap = QPixmap.fromImage(_tinted(("colonization", cache[1]))).scaled(
            px, px, Qt.KeepAspectRatio, Qt.SmoothTransformation
        )
        _colonization_pixmaps[cache] = pixmap
    return pixmap


_app_glyph_source: QImage | None = None
_app_glyphs: dict[tuple, QPixmap] = {}

# The emblem is deliberately mixed-media: orange-artwork lettering, grayscale ship/wings/outlines/rock; the source orange is strongly saturated (incl. antialiased edges), so a broad orange hue range cleanly separates accent from neutral art without baking in one exact shade.
_APP_GLYPH_ACCENT_MIN_SATURATION = 128
_APP_GLYPH_ACCENT_HUE_RANGE = range(66)


def _is_app_glyph_accent(colour: QColor) -> bool:
    return (
        colour.hsvSaturation() >= _APP_GLYPH_ACCENT_MIN_SATURATION
        and colour.hsvHue() in _APP_GLYPH_ACCENT_HUE_RANGE
    )


def app_glyph_pixmap(px: int, *, stock: bool = False) -> QPixmap:
    """The app emblem at ``px``, selectively adjusted to the HUD colours; only the source's orange shades pass through the Elite HUD matrix, grayscale pixels copied verbatim (preserving white wings/ship, dark outlines, gray rock) instead of washing the whole emblem in the HUD accent. ``stock=True`` skips the matrix and returns the original Elite orange -- the About tab uses it to stay a homage regardless of the player's colours."""
    global _app_glyph_source
    key = (px, "stock" if stock else theme.current_hud_matrix())
    pixmap = _app_glyphs.get(key)
    if pixmap is None:
        if _app_glyph_source is None:
            _app_glyph_source = QImage(str(asset_path("icon.png")))
        source = _app_glyph_source.scaled(
            px, px, Qt.KeepAspectRatio, Qt.SmoothTransformation
        )
        image = source.convertToFormat(QImage.Format_ARGB32)
        if not stock:
            for y in range(image.height()):
                for x in range(image.width()):
                    colour = image.pixelColor(x, y)
                    if colour.alpha() == 0 or not _is_app_glyph_accent(colour):
                        continue
                    rgb = theme.adjust_hud_rgb(
                        (colour.red(), colour.green(), colour.blue())
                    )
                    image.setPixelColor(x, y, QColor(*rgb, colour.alpha()))
        pixmap = QPixmap.fromImage(image)
        _app_glyphs[key] = pixmap
    return pixmap


# "Powered by" service marks (EDDN, Spansh, Raven Colonial), shown in their own brand colours (EDDN green wordmark, Spansh red-jet disc, Raven purple hex) rather than HUD-tinted; a monochrome variant is a one-liner away in git history if the mixed palette reads too loud beside the emblem.
_powered_pixmaps: dict[tuple[str, int, int], QPixmap] = {}
_powered_uris: dict[tuple[str, int, int], str] = {}


def powered_logo_pixmap(name: str, width: int, height: int) -> QPixmap:
    """A community service logo scaled to fit ``width`` x ``height`` in its original colours (aspect preserved); see the module note above for the HUD-tinting alternative."""
    key = (name, width, height)
    pixmap = _powered_pixmaps.get(key)
    if pixmap is None:
        source = QImage(str(asset_path(f"powered_{name}.png")))
        pixmap = QPixmap.fromImage(source).scaled(
            QSize(width, height), Qt.KeepAspectRatio, Qt.SmoothTransformation
        )
        _powered_pixmaps[key] = pixmap
    return pixmap


def powered_logo_html(name: str, width: int, height: int, *, alt: str = "") -> str:
    """A bundled service mark as a fitted inline image for rich text; the raster is scaled before encoding (keeping tooltip payloads small) and the resulting ``width``/``height`` retain the source aspect ratio."""
    key = (name, width, height)
    pixmap = powered_logo_pixmap(*key)
    uri = _powered_uris.get(key)
    if uri is None:
        data = QByteArray()
        buffer = QBuffer(data)
        buffer.open(QIODevice.WriteOnly)
        pixmap.save(buffer, "PNG")
        uri = "data:image/png;base64," + bytes(data.toBase64()).decode("ascii")
        _powered_uris[key] = uri
    return (
        f"<img src='{uri}' width='{pixmap.width()}' height='{pixmap.height()}' "
        f"alt='{alt}'>"
    )


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
