"""Isometric thrust and rotation gizmos for live flight input; projection and drawable geometry are plain functions so they can be reasoned about (and tested) without a screen, ``paintEvent`` only strokes what they return, and colours are read from :mod:`theme` at paint time so the player's HUD matrix flows through."""

# SPDX-License-Identifier: GPL-3.0-or-later

from __future__ import annotations

from dataclasses import dataclass
from itertools import pairwise
from math import acos, asin, atan2, cos, hypot, radians, sin

from PySide6.QtCore import QPoint, QPointF, QSize, Qt
from PySide6.QtGui import QColor, QPainter, QPen, QPolygonF
from PySide6.QtWidgets import QApplication, QWidget

from ..boost import BoostState
from ..flight_axes import FlightState
from . import theme

Vec3 = tuple[float, float, float]

# BOOST readout colours: the theme's semantic (traffic-light) hues, which bypass the HUD matrix on purpose so green/amber/red keep meaning under recoloured chrome; grey marks "no data / can't boost".
_BOOST_COLOURS = {
    BoostState.READY: "DONE",  # green
    BoostState.COOLING: "SHORT",  # red
    BoostState.IMMINENT: "READY",  # amber
}
_BOOST_GREY = QColor(120, 120, 120)

# Ship frame: +X lateral right, +Y up, +Z forward; viewed from behind and above-left, putting forward into the screen (positive depth) and keeping all six arms clear of each other.
CAMERA_YAW = radians(45.0)
CAMERA_PITCH = radians(30.0)
# How far the forward axis leans off the view axis; the automatic aim holds this fixed and varies only the lean direction, so every gizmo foreshortens alike. Derived so an un-aimed gizmo keeps the stock view.
CAMERA_TILT = acos(cos(CAMERA_YAW) * cos(CAMERA_PITCH))

# A manual aim target sits one depth unit into the screen (z = 1), this fraction of the smaller screen dimension so perspective is resolution-independent: dead-ahead reads head-on, the lean grows with crosshair offset; larger = deeper vanishing point, so gizmos lean less for the same offset.
AIM_DEPTH_SCREEN_FRACTION = 1.0

# One gizmo at scale 1.0.  Both windows are square.
BASE_SIZE = 200
_MARGIN = 26
_RING_SEGMENTS = 72
# How far to fade reference geometry behind the hub (QColor.darker percent); shading is for the frame only, never a readout.
_BACK_DARKEN = 260
# Reference geometry is thin and depth-shaded; the live readout is heavier and one flat colour, so a lit arc never dims into the frame it's read against.
_REFERENCE_WEIGHT = 2.0
_INDICATOR_WEIGHT = 6.6
# Labels track the configured UI font, not the gizmo's size: a big gizmo shouldn't shout and a small one must stay legible.
DEFAULT_FONT_PT = 10
# Elite's rotation indicators read as deflection, not attitude: full stick is a quarter turn of the ring either way.
RING_SWEEP_DEGREES = 90.0


def scaled(direction: Vec3, factor: float) -> Vec3:
    """``direction`` stretched by ``factor``, staying a three-tuple."""
    return (direction[0] * factor, direction[1] * factor, direction[2] * factor)


def aim_camera(
    dx: float, dy: float, *, tilt: float = CAMERA_TILT
) -> tuple[float, float]:
    """Camera angles pointing +Z along the screen vector ``(dx, dy)``; sits every gizmo in one imagined room aimed at the (infinitely far) screen centre, yaw turning about the vertical so up stays up and the fixed ``tilt`` keeps depth constant -- only the lean direction changes, and ``(0, 0)`` names no direction so the stock view stands."""
    length = hypot(dx, dy)
    if length < 1e-6:
        return CAMERA_YAW, CAMERA_PITCH
    ux, uy = dx / length, dy / length
    lean, into = sin(tilt), cos(tilt)
    yaw = asin(max(-1.0, min(1.0, lean * ux)))
    pitch = atan2(-lean * uy, into)
    return yaw, pitch


def aim_camera_at(dx: float, dy: float, depth: float) -> tuple[float, float]:
    """Camera angles pointing +Z at a point ``(dx, dy, depth)`` in screen space; unlike :func:`aim_camera` distance is *not* normalised, so the forward axis leans by ``atan2(hypot(dx, dy), depth)`` (dead-ahead reads head-on, off-to-the-side leans more the further out), where ``depth`` is pixels per unit into the screen (the z = 1 plane)."""
    length = hypot(dx, dy)
    if length < 1e-9:
        return 0.0, 0.0
    tilt = atan2(length, max(1e-6, depth))
    ux, uy = dx / length, dy / length
    lean, into = sin(tilt), cos(tilt)
    yaw = asin(max(-1.0, min(1.0, lean * ux)))
    pitch = atan2(-lean * uy, into)
    return yaw, pitch


def project(
    point: Vec3, *, yaw: float = CAMERA_YAW, pitch: float = CAMERA_PITCH
) -> tuple[float, float, float]:
    """Project a ship-frame point to unit screen coords plus a depth key; screen coords follow Qt (``+y`` down) and depth grows with distance from the camera, so painting in descending depth draws back-to-front."""
    x, y, z = point
    cy, sy = cos(yaw), sin(yaw)
    x1 = x * cy + z * sy
    z1 = -x * sy + z * cy
    cp, sp = cos(pitch), sin(pitch)
    y2 = y * cp + z1 * sp
    depth = -y * sp + z1 * cp
    return x1, -y2, depth


@dataclass(frozen=True, slots=True)
class Arm:
    """One of the thrust cross's six directions."""

    label: str
    direction: Vec3
    # How much of this arm is lit, 0..1 -- the 0..100 scale the pilot reads.
    fill: float = 0.0


@dataclass(frozen=True, slots=True)
class Ring:
    """One rotation ring: a circle in the plane its axis turns within."""

    label: str
    # Orthonormal basis spanning the ring's plane; sweep runs from ``u`` to ``v``.
    u: Vec3
    v: Vec3
    # Stick deflection, -1..1, drawn as -90..+90 degrees of arc.
    sweep: float = 0.0
    # Where the label sits, in ring degrees; pitch and yaw share a ``u`` so their arcs start together -- labels must be placed apart by hand or they collide.
    label_angle: float = 90.0


def thrust_arms(
    state: FlightState,
    *,
    yaw: float = CAMERA_YAW,
    pitch: float = CAMERA_PITCH,
) -> tuple[Arm, ...]:
    """The six cross arms for one input state, back-to-front; depth order depends on where the gizmo aims, so the camera must be known here rather than baked into ``Arm``."""
    arms = (
        Arm("R", (1.0, 0.0, 0.0), max(0.0, state.lateral)),
        Arm("L", (-1.0, 0.0, 0.0), max(0.0, -state.lateral)),
        Arm("U", (0.0, 1.0, 0.0), max(0.0, state.vertical)),
        Arm("D", (0.0, -1.0, 0.0), max(0.0, -state.vertical)),
        Arm("FWD", (0.0, 0.0, 1.0), max(0.0, state.throttle)),
        Arm("REV", (0.0, 0.0, -1.0), max(0.0, -state.throttle)),
    )
    return tuple(
        sorted(
            arms,
            key=lambda arm: project(arm.direction, yaw=yaw, pitch=pitch)[2],
            reverse=True,
        )
    )


def rotation_rings(state: FlightState) -> tuple[Ring, ...]:
    """The three rotation rings for one input state, each in the plane its axis turns within so the trio reads as a wire sphere (pitch upright fore-aft, yaw level, roll across the nose); pitch and roll sweep against their raw sign so the arc follows the stick, and roll's zero sits at the top (read like a bank indicator), hence its quarter-turn basis."""
    return (
        # Label angles are hand-placed where the pilot reads each axis: PITCH on the nose (+Z, angle 0 of its Z/Y basis), YAW right (+X, a quarter turn of its Z/X basis), ROLL top (+Y, angle 0 of its Y/X basis); pitch/yaw share a +Z start, so yaw's quarter turn keeps them apart.
        Ring("PITCH", (0.0, 0.0, 1.0), (0.0, 1.0, 0.0), -state.pitch, 0.0),
        Ring("YAW", (0.0, 0.0, 1.0), (1.0, 0.0, 0.0), state.yaw, 90.0),
        Ring("ROLL", (0.0, 1.0, 0.0), (-1.0, 0.0, 0.0), -state.roll, 0.0),
    )


def ring_arc(
    ring: Ring,
    radius: float,
    *,
    start: float = 0.0,
    span: float = 360.0,
    segments: int = _RING_SEGMENTS,
    yaw: float = CAMERA_YAW,
    pitch: float = CAMERA_PITCH,
) -> list[tuple[float, float, float]]:
    """Sample ``span`` degrees of a ring as projected points; sampling and projecting beats solving for the ellipse, keeping the maths obvious and right for any camera."""
    steps = max(2, int(segments * abs(span) / 360.0) + 1)
    points: list[tuple[float, float, float]] = []
    for i in range(steps):
        angle = radians(start + span * i / (steps - 1))
        c, s = cos(angle), sin(angle)
        point = (
            radius * (ring.u[0] * c + ring.v[0] * s),
            radius * (ring.u[1] * c + ring.v[1] * s),
            radius * (ring.u[2] * c + ring.v[2] * s),
        )
        points.append(project(point, yaw=yaw, pitch=pitch))
    return points


class _GizmoWidget(QWidget):
    """Shared painting scaffolding for the two gizmos."""

    def __init__(
        self,
        parent: QWidget | None = None,
        *,
        scale: float = 1.0,
        font_pt: int = DEFAULT_FONT_PT,
    ) -> None:
        super().__init__(parent)
        self._scale = max(0.25, float(scale))
        self._font_pt = max(1, int(font_pt))
        self._state = FlightState()
        self._boost_state: BoostState | None = None
        # A manual vanishing point in global screen coords, or ``None`` to aim at screen centre (the automatic default).
        self._aim_target: QPoint | None = None
        # Refreshed at the top of every paint: the aim depends on where the window currently sits.
        self._camera = (CAMERA_YAW, CAMERA_PITCH)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setMinimumSize(1, 1)

    def sizeHint(self) -> QSize:
        side = int(BASE_SIZE * self._scale)
        return QSize(side, side)

    @property
    def scale(self) -> float:
        return self._scale

    def set_scale(self, scale: float) -> None:
        scale = max(0.25, float(scale))
        if scale == self._scale:
            return
        self._scale = scale
        self.updateGeometry()
        self.resize(self.sizeHint())
        self.update()

    @property
    def font_pt(self) -> int:
        return self._font_pt

    def set_font_pt(self, font_pt: int) -> None:
        """Track the configured UI font size."""
        font_pt = max(1, int(font_pt))
        if font_pt == self._font_pt:
            return
        self._font_pt = font_pt
        self.update()

    def _apply_font(self, painter: QPainter) -> None:
        font = painter.font()
        font.setPointSizeF(self._font_pt)
        painter.setFont(font)

    @property
    def state(self) -> FlightState:
        return self._state

    def set_state(self, state: FlightState) -> None:
        """Adopt a new input state, repainting only when it differs."""
        if state == self._state:
            return
        self._state = state
        self.update()

    @property
    def boost_state(self) -> BoostState | None:
        return self._boost_state

    def set_boost_state(self, state: BoostState | None) -> None:
        """Adopt a new boost-readiness state (``None`` hides the readout)."""
        if state == self._boost_state:
            return
        self._boost_state = state
        self.update()

    @property
    def aim_target(self) -> QPoint | None:
        return self._aim_target

    def set_aim_target(self, point: QPoint | None) -> None:
        """Lean the forward axis at a fixed screen point instead of the centre; ``point`` is a global-coordinate vanishing point (the crosshair centre), ``None`` restores the automatic aim at screen centre."""
        target = QPoint(point) if point is not None else None
        if target == self._aim_target:
            return
        self._aim_target = target
        self.update()

    def _aim_depth(self) -> float:
        """Pixels to one unit into the screen (the manual target's z = 1 plane)."""
        screen = self.screen() or QApplication.primaryScreen()
        if screen is None:
            return float(BASE_SIZE)
        geometry = screen.geometry()
        return AIM_DEPTH_SCREEN_FRACTION * min(geometry.width(), geometry.height())

    def aimed_camera(self) -> tuple[float, float]:
        """Yaw/pitch leaning this gizmo's forward axis at its vanishing point; with a manual target the axis points *at* the crosshair (one depth unit into the screen, so front reads head-on and the lean tracks offset), without one it leans at screen centre with constant foreshortening."""
        here = self.mapToGlobal(self.rect().center())
        target = self._aim_target
        if target is not None:
            return aim_camera_at(
                target.x() - here.x(), target.y() - here.y(), self._aim_depth()
            )
        screen = self.screen() or QApplication.primaryScreen()
        if screen is None:
            return CAMERA_YAW, CAMERA_PITCH
        centre = screen.geometry().center()
        return aim_camera(centre.x() - here.x(), centre.y() - here.y())

    def refresh_aim(self) -> None:
        """Re-aim after the window moves; the vanishing point stayed put."""
        self.update()

    def _project(self, point: Vec3) -> tuple[float, float, float]:
        yaw, pitch = self._camera
        return project(point, yaw=yaw, pitch=pitch)

    def _radius(self) -> float:
        margin = _MARGIN * self._scale
        return max(4.0, min(self.width(), self.height()) / 2.0 - margin)

    def _centre(self) -> QPointF:
        return QPointF(self.width() / 2.0, self.height() / 2.0)

    def _to_screen(self, projected: tuple[float, float, float]) -> QPointF:
        centre = self._centre()
        return QPointF(
            centre.x() + projected[0], centre.y() + projected[1]
        )

    def _pen_width(self, weight: float = 1.0) -> float:
        return max(1.0, weight * self._scale)


class ThrustGizmo(_GizmoWidget):
    """Six-armed isometric cross: translation demand, 0..100 per direction."""

    def paintEvent(self, _event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        radius = self._radius()
        self._apply_font(painter)

        self._camera = self.aimed_camera()
        yaw, pitch = self._camera

        for arm in thrust_arms(self._state, yaw=yaw, pitch=pitch):
            end = self._to_screen(self._project(scaled(arm.direction, radius)))
            centre = self._centre()

            # Reference spar out to 100%, then the lit portion over it.
            painter.setPen(QPen(theme.GRID, self._pen_width(1.0)))
            painter.drawLine(centre, end)
            self._draw_tick(painter, arm, radius)

            if arm.fill > 0.001:
                lit = scaled(arm.direction, radius * arm.fill)
                painter.setPen(
                    QPen(
                        theme.ORANGE,
                        self._pen_width(_INDICATOR_WEIGHT),
                        Qt.SolidLine,
                        Qt.RoundCap,
                    )
                )
                painter.drawLine(centre, self._to_screen(self._project(lit)))

            self._draw_label(painter, arm, radius, end)

        self._draw_hub(painter)
        self._draw_boost(painter)

    def _draw_boost(self, painter: QPainter) -> None:
        """A status LED with a BOOST label in the top-left corner."""
        if self._boost_state is None:
            return
        if self._boost_state is BoostState.UNAVAILABLE:
            colour = _BOOST_GREY
        else:
            colour = getattr(theme, _BOOST_COLOURS[self._boost_state])

        metrics = painter.fontMetrics()
        text = "BOOST"
        dot_r = max(2.0, self._pen_width(3.0))
        gap = 5.0 * self._scale
        margin = 3.0 * self._scale
        left = margin
        baseline_y = margin + metrics.ascent()
        mid_y = baseline_y - metrics.ascent() / 2.0 + metrics.ascent() * 0.15

        painter.setPen(Qt.NoPen)
        painter.setBrush(colour)
        painter.drawEllipse(QPointF(left + dot_r, mid_y), dot_r, dot_r)
        painter.setPen(colour)
        painter.drawText(
            QPointF(left + dot_r * 2 + gap, baseline_y), text
        )

    def _draw_tick(self, painter: QPainter, arm: Arm, radius: float) -> None:
        """Scale marks across the spar, at 50% and at the 100% end."""
        centre = self._centre()
        end = self._to_screen(self._project(scaled(arm.direction, radius)))
        # Perpendicular to the spar *on screen*, so ticks read as a scale rather than drifting off-axis on the diagonal arms.
        dx, dy = end.x() - centre.x(), end.y() - centre.y()
        length = max(1e-6, (dx * dx + dy * dy) ** 0.5)
        nx, ny = -dy / length, dx / length

        painter.setPen(QPen(theme.GRID, self._pen_width(1.0)))
        for fraction in (0.5, 1.0):
            screen = self._to_screen(
                self._project(scaled(arm.direction, radius * fraction))
            )
            size = self._pen_width(1.5) * (2.2 if fraction == 1.0 else 1.2)
            painter.drawLine(
                QPointF(screen.x() - nx * size, screen.y() - ny * size),
                QPointF(screen.x() + nx * size, screen.y() + ny * size),
            )

    def _draw_label(
        self, painter: QPainter, arm: Arm, radius: float, end: QPointF
    ) -> None:
        lit = arm.fill > 0.001
        text = arm.label
        if arm.label == "REV" and self._state.reverse is None:
            # Reverse is bound somewhere we can't watch, so say so rather than imply a direction we don't know.
            text = "REV?"
        elif arm.label == "REV" and self._state.reverse:
            lit = True
        # ORANGE_DIM, not TEXT_DIM: a HUD matrix tuned for the game's chrome can crush the text greys to pure white, which would out-shout the gizmo.
        painter.setPen(theme.ORANGE if lit else theme.ORANGE_DIM)
        metrics = painter.fontMetrics()
        outward = scaled(arm.direction, radius + 10.0 * self._scale)
        anchor = self._to_screen(self._project(outward))
        painter.drawText(
            QPointF(
                anchor.x() - metrics.horizontalAdvance(text) / 2.0,
                anchor.y() + metrics.ascent() / 2.0,
            ),
            text,
        )

    def _draw_hub(self, painter: QPainter) -> None:
        painter.setPen(QPen(theme.ORANGE_DIM, self._pen_width(1.0)))
        painter.setBrush(Qt.NoBrush)
        size = self._pen_width(2.0)
        painter.drawEllipse(self._centre(), size, size)


class RotationGizmo(_GizmoWidget):
    """Three isometric rings: pitch, yaw and roll deflection, -90..+90."""

    def paintEvent(self, _event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        radius = self._radius()
        self._apply_font(painter)

        self._camera = self.aimed_camera()

        for ring in rotation_rings(self._state):
            self._draw_ring(painter, ring, radius)
        self._draw_hub(painter)

    def _arc(self, ring: Ring, radius: float, **kwargs):
        """``ring_arc`` through this gizmo's current aim."""
        yaw, pitch = self._camera
        return ring_arc(ring, radius, yaw=yaw, pitch=pitch, **kwargs)

    def _draw_ring(self, painter: QPainter, ring: Ring, radius: float) -> None:
        # The whole circle is the component, the lit arc the readout on it; segments behind the hub are drawn dimmer, which stops three same-radius circles collapsing into an unreadable tangle.
        self._draw_depth_cued(
            painter,
            self._arc(ring, radius),
            theme.GRID,
            self._pen_width(_REFERENCE_WEIGHT),
        )

        # Zero mark, and the -90/+90 limits the arc can reach.
        for angle in (-RING_SWEEP_DEGREES, 0.0, RING_SWEEP_DEGREES):
            self._draw_limit(painter, ring, radius, angle)

        if abs(ring.sweep) > 0.001:
            span = ring.sweep * RING_SWEEP_DEGREES
            # Flat and bright, never depth-shaded: the arc is the value the pilot reads, and half of it dimming would read as less input.
            painter.setPen(
                QPen(
                    theme.ORANGE,
                    self._pen_width(_INDICATOR_WEIGHT),
                    Qt.SolidLine,
                    Qt.RoundCap,
                )
            )
            painter.drawPolyline(
                QPolygonF(
                    [
                        self._to_screen(point)
                        for point in self._arc(ring, radius, start=0.0, span=span)
                    ]
                )
            )
        self._draw_label(painter, ring, radius)

    def _draw_depth_cued(
        self,
        painter: QPainter,
        points: list[tuple[float, float, float]],
        colour,
        width: float,
    ) -> None:
        """Stroke a projected polyline, fading the segments that lie behind."""
        back = QColor(colour).darker(_BACK_DARKEN)
        for first, second in pairwise(points):
            behind = (first[2] + second[2]) / 2.0 > 0.0
            painter.setPen(
                QPen(back if behind else colour, width, Qt.SolidLine, Qt.RoundCap)
            )
            painter.drawLine(self._to_screen(first), self._to_screen(second))

    def _draw_limit(
        self, painter: QPainter, ring: Ring, radius: float, angle: float
    ) -> None:
        inner = self._arc(ring, radius * 0.90, start=angle, span=0.0, segments=2)[0]
        outer = self._arc(ring, radius * 1.06, start=angle, span=0.0, segments=2)[0]
        painter.setPen(
            QPen(theme.ORANGE_DIM if angle == 0.0 else theme.GRID, self._pen_width(1.0))
        )
        painter.drawLine(self._to_screen(inner), self._to_screen(outer))

    def _draw_label(self, painter: QPainter, ring: Ring, radius: float) -> None:
        anchor = self._arc(
            ring, radius * 1.16, start=ring.label_angle, span=0.0, segments=2
        )[0]
        screen = self._to_screen(anchor)
        painter.setPen(theme.ORANGE if abs(ring.sweep) > 0.001 else theme.ORANGE_DIM)
        metrics = painter.fontMetrics()
        painter.drawText(
            QPointF(
                screen.x() - metrics.horizontalAdvance(ring.label) / 2.0,
                screen.y() + metrics.ascent() / 2.0,
            ),
            ring.label,
        )

    def _draw_hub(self, painter: QPainter) -> None:
        painter.setPen(QPen(theme.ORANGE_DIM, self._pen_width(1.0)))
        size = self._pen_width(2.0)
        painter.drawEllipse(self._centre(), size, size)


def degrees_for(sweep: float) -> float:
    """The arc a stick deflection draws, for readouts and tests."""
    return max(-1.0, min(1.0, sweep)) * RING_SWEEP_DEGREES


__all__ = [
    "BASE_SIZE",
    "CAMERA_PITCH",
    "CAMERA_YAW",
    "RING_SWEEP_DEGREES",
    "Arm",
    "Ring",
    "RotationGizmo",
    "ThrustGizmo",
    "degrees_for",
    "project",
    "ring_arc",
    "rotation_rings",
    "thrust_arms",
]
