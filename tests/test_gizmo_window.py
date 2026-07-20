
import pytest
from PySide6.QtCore import QObject, Signal

from edsc.config import Config
from edsc.flight_axes import FlightMapping, ResolvedAxis, ResolvedButton
from edsc.gui.gizmo_window import GizmoController
from edsc.platform.controller import ControllerDevice, ControllerEvent


class _FakeMonitor(QObject):
    device_connected = Signal(object)
    device_disconnected = Signal(str)
    event_received = Signal(object)
    error = Signal(str)

    def __init__(self, devices=()):
        super().__init__()
        self._devices = tuple(devices)

    @property
    def devices(self):
        return self._devices


STICK = ControllerDevice(
    "linux:231d:0200:aaa",
    "Gladiator EVO R",
    "linux-js",
    vendor_id=0x231D,
    product_id=0x0200,
    axes=6,
    axis_codes=(0, 1, 2, 3, 4, 5),
    buttons=32,
)


def _mapping() -> FlightMapping:
    return FlightMapping(
        axes={
            "roll": ResolvedAxis(STICK.id, 0, False, 0.0),
            "throttle": ResolvedAxis(STICK.id, 1, False, 0.0),
        },
        reverse=ResolvedButton(STICK.id, 4),
        reverse_is_hold=True,
        throttle_forward_only=True,
        speed_presets=((0.75, ResolvedButton(STICK.id, 26)),),
        boost=ResolvedButton(STICK.id, 2),
    )


_LOADOUT = {
    "event": "Loadout",
    "Ship": "panthermkii",
    "Modules": [
        {
            "Slot": "PowerDistributor",
            "Item": "int_powerdistributor_size7_class5",
            "Engineering": {
                "Modifiers": [{"Label": "EnginesRecharge", "Value": 5.44}]
            },
        }
    ],
}


def _controller(qapp, **overrides) -> GizmoController:
    config = Config(**overrides)
    return GizmoController(config, _FakeMonitor([STICK]))


#  visibility


def test_disabled_gizmos_stay_hidden(qapp):
    controller = _controller(qapp, gizmo_enabled=False)
    assert controller.should_show is False
    controller.refresh_visibility()
    assert not any(w.isVisible() for w in controller.windows)


def test_enabled_gizmos_show_when_flying(qapp):
    controller = _controller(qapp, gizmo_enabled=True, gizmo_in_flight_only=True)
    controller.seed_docked(False)
    assert controller.should_show is True


def test_docking_hides_the_gizmos(qapp):
    controller = _controller(qapp, gizmo_enabled=True, gizmo_in_flight_only=True)
    controller.seed_docked(True)
    assert controller.should_show is False
    # ...and undocking brings them back.
    controller.handle_journal_event({"event": "Undocked"})
    assert controller.should_show is True


def test_in_flight_only_can_be_switched_off(qapp):
    controller = _controller(qapp, gizmo_enabled=True, gizmo_in_flight_only=False)
    controller.seed_docked(True)
    assert controller.should_show is True


def test_the_refresh_clock_only_runs_while_visible(qapp):
    # Repainting a hidden translucent window over a busy game is pure waste.
    controller = _controller(qapp, gizmo_enabled=True, gizmo_in_flight_only=True)
    controller.seed_docked(False)
    assert controller._timer.isActive() is True
    controller.seed_docked(True)
    assert controller._timer.isActive() is False


def test_stop_hides_everything_and_halts_the_clock(qapp):
    controller = _controller(qapp, gizmo_enabled=True)
    controller.refresh_visibility()
    controller.stop()
    assert controller._timer.isActive() is False
    assert not any(w.isVisible() for w in controller.windows)


#  live input


def test_controller_events_reach_the_state(qapp):
    controller = _controller(qapp, gizmo_enabled=True)
    controller.apply_mapping(_mapping())
    controller.monitor.event_received.emit(
        ControllerEvent(STICK.id, "axis", 0, 32767)
    )
    controller._tick()
    assert controller.thrust.gizmo.state.roll == pytest.approx(1.0)
    assert controller.rotation.gizmo.state.roll == pytest.approx(1.0)


def test_a_tick_without_new_input_does_nothing(qapp):
    controller = _controller(qapp, gizmo_enabled=True)
    controller.apply_mapping(_mapping())
    controller._tick()
    assert controller._dirty is False
    # A second tick with nothing new must not churn.
    controller._tick()
    assert controller._dirty is False


def test_deadzone_toggle_reaches_the_tracker(qapp):
    controller = _controller(qapp, gizmo_enabled=True, gizmo_apply_deadzone=True)
    assert controller.tracker.apply_deadzones is True
    controller.set_apply_deadzone(False)
    assert controller.tracker.apply_deadzones is False
    assert controller.config.gizmo_apply_deadzone is False


#  windows


def test_game_focus_drives_click_through(qapp):
    controller = _controller(qapp, gizmo_enabled=True, auto_click_through=True)
    controller.set_game_focused(True)
    assert all(w._click_through for w in controller.windows)
    # Alt-tabbing away must hand the mouse back so they can be dragged.
    controller.set_game_focused(False)
    assert not any(w._click_through for w in controller.windows)


def test_click_through_respects_the_auto_setting(qapp):
    controller = _controller(qapp, gizmo_enabled=True, auto_click_through=False)
    controller.set_game_focused(True)
    assert not any(w._click_through for w in controller.windows)


def test_positions_round_trip_through_config(qapp):
    config = Config(
        gizmo_enabled=True,
        gizmo_thrust_x=111,
        gizmo_thrust_y=222,
        gizmo_rotation_x=333,
        gizmo_rotation_y=444,
    )
    restored = GizmoController(config, _FakeMonitor([STICK]))
    assert (restored.thrust.x(), restored.thrust.y()) == (111, 222)
    assert (restored.rotation.x(), restored.rotation.y()) == (333, 444)


def test_unplaced_gizmos_do_not_stack_on_each_other(qapp):
    # Both defaulting to (0,0) put one on top of the other in the corner, which reads as a single broken smudge -- and looks like the feature is missing.
    controller = _controller(qapp, gizmo_enabled=True)
    assert controller.thrust.pos() != controller.rotation.pos()
    assert (controller.thrust.x(), controller.thrust.y()) != (0, 0)


def test_unplaced_gizmos_land_on_the_screen(qapp):
    from PySide6.QtWidgets import QApplication as _QApp

    controller = _controller(qapp, gizmo_enabled=True)
    area = _QApp.primaryScreen().availableGeometry()
    for window in controller.windows:
        assert area.contains(window.geometry()), f"{window.geometry()} off-screen"


def test_never_placed_windows_keep_their_unset_position(qapp):
    # Saving on shutdown must not freeze a defaulted pair, or the sentinel is gone forever and the default can never apply again.
    controller = _controller(qapp, gizmo_enabled=True)
    controller.save_positions()
    assert controller.config.gizmo_thrust_x is None
    assert controller.config.gizmo_rotation_y is None


def test_dragging_persists_the_new_position(qapp):
    controller = _controller(qapp, gizmo_enabled=True)
    controller.thrust._dragged = True  # as a real drag would leave it
    controller.thrust.move(640, 480)
    controller.save_positions()
    assert (controller.config.gizmo_thrust_x, controller.config.gizmo_thrust_y) == (
        640,
        480,
    )


def test_scale_reaches_both_windows(qapp):
    controller = _controller(qapp, gizmo_enabled=True)
    controller.set_scale(2.0)
    assert controller.config.gizmo_scale == 2.0
    for window in controller.windows:
        assert window.gizmo.scale == 2.0


def test_windows_have_no_frame_and_stay_on_top(qapp):
    from PySide6.QtCore import Qt

    controller = _controller(qapp, gizmo_enabled=True)
    for window in controller.windows:
        flags = window.windowFlags()
        assert flags & Qt.FramelessWindowHint
        assert flags & Qt.WindowStaysOnTopHint
        assert window.testAttribute(Qt.WA_TranslucentBackground)


#  aim targets


def test_saved_aim_targets_restore_on_construction(qapp):
    from PySide6.QtCore import QPoint

    config = Config(
        gizmo_enabled=True,
        gizmo_thrust_target_x=800,
        gizmo_thrust_target_y=400,
    )
    controller = GizmoController(config, _FakeMonitor([STICK]))
    assert controller.thrust.gizmo.aim_target == QPoint(800, 400)
    # The rotation gizmo had no saved target, so it keeps the automatic aim.
    assert controller.rotation.gizmo.aim_target is None


def test_dragging_a_target_persists_and_re_aims(qapp):
    controller = _controller(qapp, gizmo_enabled=True)
    window = controller.thrust_target
    window.move(1000, 600)  # where a drag would leave it
    window.dragged.emit()

    point = window.aim_point()
    assert (
        controller.config.gizmo_thrust_target_x,
        controller.config.gizmo_thrust_target_y,
    ) == (point.x(), point.y())
    assert controller.thrust.gizmo.aim_target == point


def test_showing_then_hiding_untouched_targets_reverts_the_aim(qapp):
    controller = _controller(qapp, gizmo_enabled=True)
    controller.show_targets()
    # Every crosshair previews a point, so the gizmos aim at them while shown.
    assert controller.thrust.gizmo.aim_target is not None
    assert all(
        w.isVisible() for w in (controller.thrust_target, controller.rotation_target)
    )

    controller.hide_targets()
    # Nothing was dragged and nothing saved, so it falls back to the auto aim.
    assert controller.thrust.gizmo.aim_target is None
    assert not any(
        w.isVisible() for w in (controller.thrust_target, controller.rotation_target)
    )


def test_a_dragged_target_survives_hiding(qapp):
    controller = _controller(qapp, gizmo_enabled=True)
    controller.show_targets()
    window = controller.rotation_target
    window.move(1234, 900)
    window.dragged.emit()
    dragged = window.aim_point()

    controller.hide_targets()
    assert controller.rotation.gizmo.aim_target == dragged


def test_stop_hides_the_aim_targets(qapp):
    controller = _controller(qapp, gizmo_enabled=True)
    controller.show_targets()
    controller.stop()
    assert not any(
        w.isVisible() for w in (controller.thrust_target, controller.rotation_target)
    )


def test_aim_target_crosshair_actually_paints(qapp):
    from PySide6.QtCore import QPoint, QSize
    from PySide6.QtGui import QImage
    from PySide6.QtWidgets import QWidget

    from edsc.gui.gizmo_window import AIM_TARGET_SIZE, AimTarget

    crosshair = AimTarget()
    image = QImage(QSize(AIM_TARGET_SIZE, AIM_TARGET_SIZE), QImage.Format_ARGB32)
    image.fill(0)
    crosshair.render(
        image, QPoint(0, 0), crosshair.rect(), QWidget.RenderFlag.DrawChildren
    )
    painted = sum(
        1
        for x in range(AIM_TARGET_SIZE)
        for y in range(AIM_TARGET_SIZE)
        if image.pixelColor(x, y).alpha()
    )
    assert painted > 0


def test_aim_target_windows_are_frameless_and_on_top(qapp):
    from PySide6.QtCore import Qt

    controller = _controller(qapp, gizmo_enabled=True)
    for window in (controller.thrust_target, controller.rotation_target):
        flags = window.windowFlags()
        assert flags & Qt.FramelessWindowHint
        assert flags & Qt.WindowStaysOnTopHint
        assert window.testAttribute(Qt.WA_TranslucentBackground)


#  mapping persistence


def test_a_saved_mapping_is_restored_on_construction(qapp):
    config = Config(gizmo_enabled=True, flight_mapping=_mapping().to_config())
    controller = GizmoController(config, _FakeMonitor([STICK]))
    assert controller.mapping.axes["roll"] == ResolvedAxis(STICK.id, 0, False, 0.0)
    assert controller.mapping.reverse == ResolvedButton(STICK.id, 4)
    assert controller.mapping.is_empty is False


def test_apply_mapping_writes_through_to_config(qapp):
    controller = _controller(qapp, gizmo_enabled=True)
    assert controller.mapping.is_empty is True
    controller.apply_mapping(_mapping())
    assert controller.config.flight_mapping["axes"]["roll"]["index"] == 0


#  boost readout


def test_loadout_event_sets_the_boost_ship(qapp):
    from edsc.boost import BoostState

    controller = _controller(qapp, gizmo_enabled=True)
    controller.apply_mapping(_mapping())
    controller.handle_journal_event(_LOADOUT)
    assert controller.boost.available is True
    assert controller.boost.state is BoostState.READY
    assert controller.boost.interval == 6.5  # Panther Clipper, from EDSY


def test_a_boost_press_starts_the_cooldown(qapp):
    from edsc.boost import BoostState

    controller = _controller(qapp, gizmo_enabled=True)
    controller.apply_mapping(_mapping())
    controller.handle_journal_event(_LOADOUT)

    # The bound boost button (Joy_3 -> index 2) fires.
    controller.monitor.event_received.emit(
        ControllerEvent(STICK.id, "button", 2, 1)
    )
    assert controller.boost.state is BoostState.COOLING
    assert controller.boost.remaining == pytest.approx(6.5)


def test_an_unbound_button_does_not_boost(qapp):
    controller = _controller(qapp, gizmo_enabled=True)
    controller.apply_mapping(_mapping())
    controller.handle_journal_event(_LOADOUT)
    # A different button must not trigger a boost.
    controller.monitor.event_received.emit(
        ControllerEvent(STICK.id, "button", 7, 1)
    )
    assert controller.boost.available is True
    assert controller.boost.fraction == 1.0


def test_tick_feeds_boost_state_to_the_thrust_gizmo(qapp):
    from edsc.boost import BoostState

    controller = _controller(qapp, gizmo_enabled=True)
    controller.apply_mapping(_mapping())
    controller.handle_journal_event(_LOADOUT)
    controller._tick()
    assert controller.thrust.gizmo.boost_state is BoostState.READY
    # The rotation gizmo never shows a boost readout.
    assert controller.rotation.gizmo.boost_state is None


def test_set_journal_dir_seeds_ship_from_the_latest_loadout(qapp, tmp_path):
    import json

    from edsc.boost import BoostState

    journal = tmp_path / "Journal.2026-07-17T120000.01.log"
    journal.write_text(json.dumps(_LOADOUT) + "\n", encoding="utf-8")

    controller = _controller(qapp, gizmo_enabled=True)
    controller.set_journal_dir(tmp_path)
    assert controller.boost.available is True  # ship seeded from the Loadout
    assert controller.boost.state is BoostState.READY
    controller.boost.boost()
    assert controller.boost.state is BoostState.COOLING
    assert controller.boost.remaining == pytest.approx(6.5)  # Panther interval
