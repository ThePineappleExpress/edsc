
import pytest
from PySide6.QtWidgets import QApplication

from edsc.flight_axes import FlightState
from edsc.gui import gizmo

#  projection


def test_origin_projects_to_the_centre():
    x, y, depth = gizmo.project((0.0, 0.0, 0.0))
    assert (x, y) == (0.0, 0.0)
    assert depth == 0.0


def test_vertical_projects_straight_up():
    # Qt's y grows downward, so "up" is negative.
    x, y, _ = gizmo.project((0.0, 1.0, 0.0))
    assert x == pytest.approx(0.0, abs=1e-9)
    assert y < 0.0


def test_forward_recedes_into_the_screen():
    # The whole point of the camera: +Z must be farther away than -Z.
    _, _, forward = gizmo.project((0.0, 0.0, 1.0))
    _, _, backward = gizmo.project((0.0, 0.0, -1.0))
    assert forward > 0.0
    assert backward < forward


def test_lateral_projects_to_the_right():
    x, _, _ = gizmo.project((1.0, 0.0, 0.0))
    assert x > 0.0


def test_projection_is_linear():
    single = gizmo.project((1.0, 2.0, 3.0))
    double = gizmo.project((2.0, 4.0, 6.0))
    for one, two in zip(single, double, strict=True):
        assert two == pytest.approx(one * 2.0)


def test_opposite_points_mirror_through_the_centre():
    plus = gizmo.project((0.4, -0.2, 0.7))
    minus = gizmo.project((-0.4, 0.2, -0.7))
    for one, two in zip(plus, minus, strict=True):
        assert two == pytest.approx(-one)


def test_the_six_arms_do_not_collide_on_screen():
    # Every arm needs its own direction, or the cross is unreadable.
    angles = set()
    for arm in gizmo.thrust_arms(FlightState()):
        x, y, _ = gizmo.project(arm.direction)
        angles.add((round(x, 3), round(y, 3)))
    assert len(angles) == 6


#  thrust arms


def test_thrust_arms_split_each_axis_across_two_directions():
    arms = {a.label: a for a in gizmo.thrust_arms(FlightState(lateral=0.6))}
    assert arms["R"].fill == pytest.approx(0.6)
    assert arms["L"].fill == 0.0

    arms = {a.label: a for a in gizmo.thrust_arms(FlightState(lateral=-0.6))}
    assert arms["L"].fill == pytest.approx(0.6)
    assert arms["R"].fill == 0.0


def test_thrust_arms_cover_all_six_directions():
    arms = gizmo.thrust_arms(FlightState())
    assert {a.label for a in arms} == {"R", "L", "U", "D", "FWD", "REV"}
    assert len(arms) == 6


def test_negative_throttle_lights_the_reverse_arm():
    arms = {a.label: a for a in gizmo.thrust_arms(FlightState(throttle=-0.6))}
    assert arms["REV"].fill == pytest.approx(0.6)
    assert arms["FWD"].fill == 0.0


def test_thrust_arms_are_sorted_back_to_front():
    depths = [
        gizmo.project(arm.direction)[2] for arm in gizmo.thrust_arms(FlightState())
    ]
    assert depths == sorted(depths, reverse=True)


def test_thrust_arms_resort_when_the_camera_aims_elsewhere():
    # Depth order depends on the aim, so it cannot be baked into Arm.
    yaw, pitch = gizmo.aim_camera(-1.0, 0.5)  # aiming the other way
    arms = gizmo.thrust_arms(FlightState(), yaw=yaw, pitch=pitch)
    depths = [gizmo.project(a.direction, yaw=yaw, pitch=pitch)[2] for a in arms]
    assert depths == sorted(depths, reverse=True)
    # ...and that really is a different order from the stock camera.
    stock = [a.label for a in gizmo.thrust_arms(FlightState())]
    assert [a.label for a in arms] != stock


#  rotation rings


def test_rotation_rings_carry_each_axis():
    state = FlightState(roll=0.5, pitch=-0.8, yaw=0.3)
    rings = {r.label: r for r in gizmo.rotation_rings(state)}
    # Pitch and roll sweep against their raw sign so the arc follows the stick; yaw reads straight through.
    assert rings["ROLL"].sweep == pytest.approx(-0.5)
    assert rings["PITCH"].sweep == pytest.approx(0.8)
    assert rings["YAW"].sweep == pytest.approx(0.3)


def test_ring_planes_are_perpendicular_to_their_axis():
    rings = {r.label: r for r in gizmo.rotation_rings(FlightState())}
    # Each ring's basis must span the plane its axis turns within; roll's is a quarter turn from the obvious one so its zero lands at the top.
    assert rings["ROLL"].u == (0.0, 1.0, 0.0) and rings["ROLL"].v == (-1.0, 0.0, 0.0)
    assert rings["PITCH"].u == (0.0, 0.0, 1.0) and rings["PITCH"].v == (0.0, 1.0, 0.0)
    assert rings["YAW"].u == (0.0, 0.0, 1.0) and rings["YAW"].v == (1.0, 0.0, 0.0)


def test_roll_zero_sits_at_the_top_of_its_ring():
    # Read like a bank indicator: zero at 12 o'clock, not out at 3 o'clock.
    roll = {r.label: r for r in gizmo.rotation_rings(FlightState())}["ROLL"]
    x, y, _ = gizmo.ring_arc(roll, 1.0, start=0.0, span=0.0, segments=2)[0]
    assert x == pytest.approx(0.0, abs=1e-6)
    assert y < -0.5  # Qt y grows downward, so up is negative


#  aiming at the vanishing point


@pytest.mark.parametrize(
    "dx,dy",
    [(1.0, -0.5), (-1.0, -0.5), (1.0, 0.5), (-1.0, 0.5), (1.0, 0.0), (0.0, -1.0)],
)
def test_forward_points_at_the_vanishing_point(dx, dy):
    from math import atan2, degrees

    yaw, pitch = gizmo.aim_camera(dx, dy)
    fx, fy, _ = gizmo.project((0.0, 0.0, 1.0), yaw=yaw, pitch=pitch)
    wanted = degrees(atan2(dy, dx)) % 360
    got = degrees(atan2(fy, fx)) % 360
    assert (wanted - got + 180) % 360 - 180 == pytest.approx(0.0, abs=1e-6)


@pytest.mark.parametrize(
    "dx,dy", [(1.0, -0.5), (-1.0, -0.5), (1.0, 0.5), (-1.0, 0.5), (0.0, 1.0)]
)
def test_up_stays_vertical_at_every_aim(dx, dy):
    # Yaw turns about the vertical, so aiming must never tilt the U/D spar.
    yaw, pitch = gizmo.aim_camera(dx, dy)
    x, y, _ = gizmo.project((0.0, 1.0, 0.0), yaw=yaw, pitch=pitch)
    assert x == pytest.approx(0.0, abs=1e-9)
    assert y < 0.0


@pytest.mark.parametrize("dx,dy", [(1.0, -0.5), (-1.0, 0.5), (1.0, 0.0)])
def test_aiming_keeps_foreshortening_constant(dx, dy):
    # Only the lean's direction changes, so every gizmo looks equally deep.
    yaw, pitch = gizmo.aim_camera(dx, dy)
    _, _, depth = gizmo.project((0.0, 0.0, 1.0), yaw=yaw, pitch=pitch)
    stock = gizmo.project((0.0, 0.0, 1.0))[2]
    assert depth == pytest.approx(stock)


def test_aiming_from_the_default_spot_recovers_the_stock_camera():
    # The gizmos default to below-left of centre; that must look unchanged.
    stock = gizmo.project((0.0, 0.0, 1.0))
    fx, fy, _ = stock
    yaw, pitch = gizmo.aim_camera(fx, fy)
    assert yaw == pytest.approx(gizmo.CAMERA_YAW)
    assert pitch == pytest.approx(gizmo.CAMERA_PITCH)


def test_dead_centre_has_no_direction_and_keeps_the_stock_view():
    assert gizmo.aim_camera(0.0, 0.0) == (gizmo.CAMERA_YAW, gizmo.CAMERA_PITCH)


def test_a_manual_aim_target_points_at_the_crosshair_with_depth(qapp):
    from PySide6.QtCore import QPoint

    widget = gizmo.ThrustGizmo()
    widget.resize(gizmo.BASE_SIZE, gizmo.BASE_SIZE)
    widget.move(0, 0)
    here = widget.mapToGlobal(widget.rect().center())
    target = QPoint(here.x() + 300, here.y() - 120)
    widget.set_aim_target(target)
    assert widget.aim_target == target
    # The forward axis points at the target as a 3-D point one depth unit in.
    assert widget.aimed_camera() == pytest.approx(
        gizmo.aim_camera_at(
            target.x() - here.x(), target.y() - here.y(), widget._aim_depth()
        )
    )


def test_a_target_dead_ahead_reads_head_on(qapp):
    # The reported bug: a crosshair in front of the gizmo must point straight into the screen, not sit at the stock isometric lean.
    widget = gizmo.ThrustGizmo()
    widget.resize(gizmo.BASE_SIZE, gizmo.BASE_SIZE)
    widget.move(0, 0)
    widget.set_aim_target(widget.mapToGlobal(widget.rect().center()))
    yaw, pitch = widget.aimed_camera()
    assert (yaw, pitch) == pytest.approx((0.0, 0.0))
    # Forward then projects to the centre with pure depth -- no screen extent.
    fx, fy, depth = gizmo.project((0.0, 0.0, 1.0), yaw=yaw, pitch=pitch)
    assert (fx, fy) == pytest.approx((0.0, 0.0))
    assert depth > 0.0


def test_a_target_further_out_leans_the_forward_axis_more(qapp):
    # Distance must not be normalised away: the further the crosshair sits, the more the forward axis leans towards it.
    from math import hypot

    def forward_lean(offset):
        yaw, pitch = gizmo.aim_camera_at(offset, 0.0, 1000.0)
        fx, fy, _ = gizmo.project((0.0, 0.0, 1.0), yaw=yaw, pitch=pitch)
        return hypot(fx, fy)

    assert forward_lean(200.0) < forward_lean(900.0)


def test_clearing_the_aim_target_returns_to_the_auto_aim(qapp):
    from PySide6.QtCore import QPoint

    widget = gizmo.ThrustGizmo()
    widget.resize(gizmo.BASE_SIZE, gizmo.BASE_SIZE)
    widget.move(0, 0)
    widget.set_aim_target(QPoint(9999, -9999))
    widget.set_aim_target(None)
    assert widget.aim_target is None
    screen = widget.screen() or QApplication.primaryScreen()
    centre = screen.geometry().center()
    here = widget.mapToGlobal(widget.rect().center())
    assert widget.aimed_camera() == pytest.approx(
        gizmo.aim_camera(centre.x() - here.x(), centre.y() - here.y())
    )


def test_gizmos_either_side_of_centre_mirror_each_other():
    # Placed left and right, the pair leans towards each other -- the mirrored look, now falling out of where they sit.
    left = gizmo.aim_camera(1.0, -0.5)
    right = gizmo.aim_camera(-1.0, -0.5)
    lx, ly, ld = gizmo.project((0.0, 0.0, 1.0), yaw=left[0], pitch=left[1])
    rx, ry, rd = gizmo.project((0.0, 0.0, 1.0), yaw=right[0], pitch=right[1])
    assert lx == pytest.approx(-rx)
    assert ly == pytest.approx(ry)
    assert ld == pytest.approx(rd)


def test_aim_is_unaffected_by_distance_only_direction():
    near = gizmo.aim_camera(2.0, -1.0)
    far = gizmo.aim_camera(2000.0, -1000.0)
    assert near == pytest.approx(far)


def test_ring_labels_sit_where_the_pilot_reads_each_axis():
    # PITCH on the nose (+Z forward), YAW out to the right (+X), ROLL up top (+Y); compare the pre-projection label direction so it is camera-agnostic.
    from math import cos, radians, sin

    def label_direction(ring):
        angle = radians(ring.label_angle)
        return tuple(
            ring.u[i] * cos(angle) + ring.v[i] * sin(angle) for i in range(3)
        )

    rings = {r.label: r for r in gizmo.rotation_rings(FlightState())}
    assert label_direction(rings["PITCH"]) == pytest.approx((0.0, 0.0, 1.0))
    assert label_direction(rings["YAW"]) == pytest.approx((1.0, 0.0, 0.0))
    assert label_direction(rings["ROLL"]) == pytest.approx((0.0, 1.0, 0.0))


def test_ring_labels_are_placed_apart():
    # Pitch and yaw share a `u`, so identical label angles would overlay them.
    rings = gizmo.rotation_rings(FlightState())
    anchors = set()
    for ring in rings:
        point = gizmo.ring_arc(
            ring, 1.0, start=ring.label_angle, span=0.0, segments=2
        )[0]
        anchors.add((round(point[0], 3), round(point[1], 3)))
    assert len(anchors) == len(rings)


def test_ring_arc_samples_lie_on_the_circle():
    ring = gizmo.rotation_rings(FlightState())[2]  # ROLL, in the X-Y plane
    for x, y, _ in gizmo.ring_arc(ring, 1.0, segments=12):
        # Projection is linear, so a unit circle stays within a unit radius.
        assert x * x + y * y <= 1.0 + 1e-6


def test_ring_arc_closes_the_full_circle():
    ring = gizmo.rotation_rings(FlightState())[0]
    points = gizmo.ring_arc(ring, 1.0)
    assert points[0] == pytest.approx(points[-1])


def test_ring_arc_span_controls_the_sample_count():
    ring = gizmo.rotation_rings(FlightState())[0]
    assert len(gizmo.ring_arc(ring, 1.0, span=0.0, segments=2)) == 2
    assert len(gizmo.ring_arc(ring, 1.0, span=360.0)) > len(
        gizmo.ring_arc(ring, 1.0, span=90.0)
    )


def test_degrees_for_clamps_to_the_sweep_limit():
    assert gizmo.degrees_for(1.0) == gizmo.RING_SWEEP_DEGREES
    assert gizmo.degrees_for(-1.0) == -gizmo.RING_SWEEP_DEGREES
    assert gizmo.degrees_for(0.5) == gizmo.RING_SWEEP_DEGREES / 2
    assert gizmo.degrees_for(9.0) == gizmo.RING_SWEEP_DEGREES


#  widgets


@pytest.mark.parametrize("widget_class", [gizmo.ThrustGizmo, gizmo.RotationGizmo])
def test_widgets_paint_without_a_screen(qapp, widget_class):
    from PySide6.QtCore import QPoint, QSize
    from PySide6.QtGui import QImage
    from PySide6.QtWidgets import QWidget

    widget = widget_class(scale=1.0)
    widget.resize(gizmo.BASE_SIZE, gizmo.BASE_SIZE)
    widget.set_state(
        FlightState(lateral=0.6, vertical=-0.4, throttle=0.75, roll=0.5, pitch=-0.8)
    )
    image = QImage(QSize(gizmo.BASE_SIZE, gizmo.BASE_SIZE), QImage.Format_ARGB32)
    image.fill(0)
    widget.render(image, QPoint(0, 0), widget.rect(), QWidget.RenderFlag.DrawChildren)
    # Something was actually stroked.
    painted = sum(
        1
        for x in range(0, gizmo.BASE_SIZE, 4)
        for y in range(0, gizmo.BASE_SIZE, 4)
        if image.pixelColor(x, y).alpha()
    )
    assert painted > 0


@pytest.mark.parametrize("widget_class", [gizmo.ThrustGizmo, gizmo.RotationGizmo])
def test_scale_drives_the_size_hint(qapp, widget_class):
    widget = widget_class(scale=1.0)
    assert widget.sizeHint().width() == gizmo.BASE_SIZE
    widget.set_scale(2.0)
    assert widget.sizeHint().width() == gizmo.BASE_SIZE * 2
    # Absurd scales must not produce a zero or negative widget.
    widget.set_scale(0.01)
    assert widget.sizeHint().width() > 0


def test_font_follows_the_ui_setting_not_the_gizmo_scale(qapp):
    # A big gizmo must not shout and a small one must stay legible, so the labels track the configured UI font instead of the widget's size.
    widget = gizmo.ThrustGizmo(scale=1.0, font_pt=13)
    assert widget.font_pt == 13
    widget.set_scale(2.5)
    assert widget.font_pt == 13
    widget.set_font_pt(8)
    assert widget.font_pt == 8


def test_font_size_is_never_degenerate(qapp):
    widget = gizmo.ThrustGizmo(font_pt=0)
    assert widget.font_pt >= 1
    widget.set_font_pt(-5)
    assert widget.font_pt >= 1


def test_indicator_strokes_outweigh_the_reference_frame(qapp):
    # The readout has to win against the frame it is read against.
    assert gizmo._INDICATOR_WEIGHT > gizmo._REFERENCE_WEIGHT


def test_setting_the_same_state_does_not_repaint(qapp):
    widget = gizmo.ThrustGizmo()
    state = FlightState(lateral=0.5)
    widget.set_state(state)
    assert widget.state == state
    # Idempotent: the 30Hz timer pushes state every tick whether it moved or not.
    widget.set_state(FlightState(lateral=0.5))
    assert widget.state == state
