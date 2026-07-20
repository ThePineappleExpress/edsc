
import pytest

from edsc import flight_axes
from edsc.binds import AxisBinding, ButtonBinding, FlightBinds
from edsc.flight_axes import (
    FlightMapping,
    FlightTracker,
    ResolvedAxis,
    ResolvedButton,
    resolve_mapping,
)
from edsc.platform.controller import ControllerDevice, ControllerEvent

# A full contiguous ABS set, as the VKB sticks report.
_FULL_CODES = (0, 1, 2, 3, 4, 5, 6, 7, 16, 17)
# Only X, Y and RZ declared, so joydev packs RZ into index 2 -- the case a static Joy_*Axis -> index table gets wrong.
_SPARSE_CODES = (0, 1, 5)

STICK = ControllerDevice(
    "linux:231d:0200:aaa",
    "Gladiator EVO R",
    "linux-js",
    vendor_id=0x231D,
    product_id=0x0200,
    axes=10,
    axis_codes=_FULL_CODES,
    buttons=80,
)
THROTTLE = ControllerDevice(
    "linux:231d:3201:bbb",
    "Gladiator EVO OT L",
    "linux-js",
    vendor_id=0x231D,
    product_id=0x3201,
    axes=10,
    axis_codes=_FULL_CODES,
    buttons=80,
)


def _binds(**overrides) -> FlightBinds:
    """Bindings shaped like the real Custom.4.2.binds, tweakable per test."""
    base = {
        "preset": "Custom",
        "axes": {
            "roll": AxisBinding("231D0200", "Joy_XAxis", False, 0.0),
            "pitch": AxisBinding("231D0200", "Joy_YAxis", True, 0.0),
            "yaw": AxisBinding("231D0200", "Joy_RZAxis", True, 0.0),
            "lateral": AxisBinding("231D3201", "Joy_XAxis", True, 0.0),
            "vertical": AxisBinding("231D3201", "Joy_RZAxis", True, 0.0),
            "throttle": AxisBinding("231D3201", "Joy_YAxis", False, 0.0),
        },
        "throttle_forward_only": True,
        "reverse": ButtonBinding("231D3201", "Joy_5"),
        "reverse_is_hold": True,
        "speed_presets": ((0.75, ButtonBinding("231D3201", "Joy_27")),),
    }
    base.update(overrides)
    return FlightBinds(**base)


def _axis_event(device: ControllerDevice, index: int, value: int, **kw):
    return ControllerEvent(device.id, "axis", index, value, **kw)


def _button_event(device: ControllerDevice, index: int, value: int, **kw):
    return ControllerEvent(device.id, "button", index, value, **kw)


#  names and maths


def test_button_index_is_one_based():
    # Elite numbers buttons from 1; no preset it ships ever says Joy_0.
    assert flight_axes.button_index("Joy_1") == 0
    assert flight_axes.button_index("Joy_27") == 26
    assert flight_axes.button_index("Joy_0") is None
    assert flight_axes.button_index("Joy_POV1Up") is None
    assert flight_axes.button_index("Joy_XAxis") is None


def test_deadzone_zeroes_the_centre_and_rescales_the_rest():
    assert flight_axes.apply_deadzone(0.05, 0.1) == 0.0
    assert flight_axes.apply_deadzone(-0.05, 0.1) == 0.0
    # Just outside the deadzone starts from zero, and the extreme still reaches 1.
    assert flight_axes.apply_deadzone(0.1, 0.1) == 0.0
    assert flight_axes.apply_deadzone(1.0, 0.1) == pytest.approx(1.0)
    assert flight_axes.apply_deadzone(-1.0, 0.1) == pytest.approx(-1.0)
    assert flight_axes.apply_deadzone(0.55, 0.1) == pytest.approx(0.5)
    # No deadzone is a pass-through.
    assert flight_axes.apply_deadzone(0.42, 0.0) == 0.42


#  resolution


def test_resolves_every_axis_to_a_live_index():
    mapping = resolve_mapping(_binds(), [STICK, THROTTLE])
    assert mapping.unresolved == ()
    assert mapping.axes["roll"] == ResolvedAxis(STICK.id, 0, False, 0.0)
    assert mapping.axes["pitch"] == ResolvedAxis(STICK.id, 1, True, 0.0)
    assert mapping.axes["yaw"] == ResolvedAxis(STICK.id, 5, True, 0.0)
    assert mapping.axes["throttle"] == ResolvedAxis(THROTTLE.id, 1, False, 0.0)
    assert mapping.reverse == ResolvedButton(THROTTLE.id, 4)
    assert mapping.speed_presets == ((0.75, ResolvedButton(THROTTLE.id, 26)),)
    assert mapping.device_ids == (STICK.id, THROTTLE.id)


def test_resolves_through_dense_axis_packing():
    # The same Joy_RZAxis lands on index 2, not 5, when the stick declares less.
    sparse = ControllerDevice(
        "linux:231d:0200:aaa",
        "Sparse stick",
        "linux-js",
        vendor_id=0x231D,
        product_id=0x0200,
        axes=3,
        axis_codes=_SPARSE_CODES,
    )
    mapping = resolve_mapping(_binds(), [sparse, THROTTLE])
    assert mapping.axes["yaw"].index == 2


def test_symbolic_device_names_resolve_through_mappings():
    binds = _binds(
        axes={"roll": AxisBinding("SaitekX52", "Joy_XAxis", False, 0.0)},
        reverse=None,
        speed_presets=(),
    )
    saitek = ControllerDevice(
        "linux:06a3:0255:ccc",
        "X52",
        "linux-js",
        vendor_id=0x06A3,
        product_id=0x0255,
        axes=6,
        axis_codes=(0, 1, 2, 3, 4, 5),
    )
    mapping = resolve_mapping(
        binds, [saitek], {"SaitekX52": ("06A3075C", "06A30255")}
    )
    assert mapping.axes["roll"] == ResolvedAxis(saitek.id, 0, False, 0.0)


def test_unresolved_bindings_are_reported_not_dropped_silently():
    mapping = resolve_mapping(_binds(), [STICK])  # throttle stick absent
    assert mapping.axes.keys() == {"roll", "pitch", "yaw"}
    assert any("not connected" in reason for reason in mapping.unresolved)


def test_identical_devices_are_reported_rather_than_guessed():
    twin = ControllerDevice(
        "linux:231d:0200:zzz",
        "Gladiator EVO R (2)",
        "linux-js",
        vendor_id=0x231D,
        product_id=0x0200,
        axes=10,
        axis_codes=_FULL_CODES,
    )
    mapping = resolve_mapping(_binds(), [STICK, twin, THROTTLE])
    assert "roll" not in mapping.axes
    assert any("identical" in reason for reason in mapping.unresolved)


def test_backend_without_an_axis_map_resolves_nothing():
    # SDL2 reports no axis codes, which is why Windows remaps by hand.
    sdl = ControllerDevice(
        "sdl:0", "Stick", "sdl2", vendor_id=0x231D, product_id=0x0200, axes=6
    )
    mapping = resolve_mapping(_binds(), [sdl, THROTTLE])
    assert "roll" not in mapping.axes
    assert any("which axis is which" in reason for reason in mapping.unresolved)


def test_slider_axes_stay_unresolved():
    binds = _binds(
        axes={"roll": AxisBinding("231D0200", "Joy_UAxis", False, 0.0)},
        reverse=None,
        speed_presets=(),
    )
    mapping = resolve_mapping(binds, [STICK])
    assert mapping.axes == {}
    assert any("no kernel axis equivalent" in r for r in mapping.unresolved)


#  live state


def test_axes_normalise_and_invert():
    tracker = FlightTracker(resolve_mapping(_binds(), [STICK, THROTTLE]))
    tracker.handle(_axis_event(STICK, 0, 32767))  # roll, not inverted
    tracker.handle(_axis_event(STICK, 1, 32767))  # pitch, inverted
    state = tracker.state
    assert state.roll == pytest.approx(1.0)
    assert state.pitch == pytest.approx(-1.0)


def test_deadzone_can_be_switched_off():
    binds = _binds(
        axes={"roll": AxisBinding("231D0200", "Joy_XAxis", False, 0.5)},
        reverse=None,
        speed_presets=(),
    )
    mapping = resolve_mapping(binds, [STICK])
    event = _axis_event(STICK, 0, 32767 // 4)  # ~0.25, inside the deadzone

    applied = FlightTracker(mapping, apply_deadzones=True)
    applied.handle(event)
    assert applied.state.roll == 0.0

    raw = FlightTracker(mapping, apply_deadzones=False)
    raw.handle(event)
    assert raw.state.roll == pytest.approx(0.25, abs=1e-3)


def test_forward_only_throttle_maps_full_travel_to_zero_through_one():
    tracker = FlightTracker(resolve_mapping(_binds(), [STICK, THROTTLE]))
    # Lever fully back is a stop, not full reverse.
    tracker.handle(_axis_event(THROTTLE, 1, -32768))
    assert tracker.state.throttle == pytest.approx(0.0)
    tracker.handle(_axis_event(THROTTLE, 1, 0))
    assert tracker.state.throttle == pytest.approx(0.5, abs=1e-3)
    tracker.handle(_axis_event(THROTTLE, 1, 32767))
    assert tracker.state.throttle == pytest.approx(1.0)


def test_hold_reverse_flips_the_sign_only_while_held():
    tracker = FlightTracker(resolve_mapping(_binds(), [STICK, THROTTLE]))
    tracker.handle(_axis_event(THROTTLE, 1, 32767))
    assert tracker.state.throttle == pytest.approx(1.0)
    assert tracker.state.reverse is False

    tracker.handle(_button_event(THROTTLE, 4, 1))
    assert tracker.state.throttle == pytest.approx(-1.0)
    assert tracker.state.reverse is True

    tracker.handle(_button_event(THROTTLE, 4, 0))
    assert tracker.state.throttle == pytest.approx(1.0)


def test_hold_reverse_is_known_from_the_initial_report():
    # joydev replays held buttons when a device opens, so hold needs no guess.
    tracker = FlightTracker(resolve_mapping(_binds(), [STICK, THROTTLE]))
    tracker.handle(_button_event(THROTTLE, 4, 1, initial=True))
    tracker.handle(_axis_event(THROTTLE, 1, 32767, initial=True))
    assert tracker.state.reverse is True
    assert tracker.state.throttle == pytest.approx(-1.0)


def test_toggle_reverse_starts_forward_and_flips_on_each_press():
    tracker = FlightTracker(
        resolve_mapping(_binds(reverse_is_hold=False), [STICK, THROTTLE])
    )
    tracker.handle(_axis_event(THROTTLE, 1, 32767))
    assert tracker.state.reverse is False

    tracker.handle(_button_event(THROTTLE, 4, 1))
    tracker.handle(_button_event(THROTTLE, 4, 0))  # release must not flip back
    assert tracker.state.reverse is True

    tracker.handle(_button_event(THROTTLE, 4, 1))
    assert tracker.state.reverse is False


def test_toggle_reverse_ignores_the_initial_report():
    tracker = FlightTracker(
        resolve_mapping(_binds(reverse_is_hold=False), [STICK, THROTTLE])
    )
    tracker.handle(_button_event(THROTTLE, 4, 1, initial=True))
    assert tracker.state.reverse is False


def test_preset_holds_until_the_lever_moves():
    tracker = FlightTracker(resolve_mapping(_binds(), [STICK, THROTTLE]))
    tracker.handle(_axis_event(THROTTLE, 1, 32767))
    assert tracker.state.throttle == pytest.approx(1.0)

    tracker.handle(_button_event(THROTTLE, 26, 1))  # SetSpeed75
    assert tracker.state.throttle == pytest.approx(0.75)

    # The lever still reads full forward, but the game is at 75% until it moves.
    tracker.handle(_axis_event(THROTTLE, 1, 0))
    assert tracker.state.throttle == pytest.approx(0.5, abs=1e-3)


def test_signed_preset_resyncs_a_toggled_reverse():
    binds = _binds(
        reverse_is_hold=False,
        speed_presets=(
            (-0.50, ButtonBinding("231D3201", "Joy_28")),
            (0.75, ButtonBinding("231D3201", "Joy_27")),
        ),
    )
    tracker = FlightTracker(resolve_mapping(binds, [STICK, THROTTLE]))
    # Drift the mirror out of step with the game.
    tracker.handle(_button_event(THROTTLE, 4, 1))
    assert tracker.state.reverse is True

    # A negative preset states the direction outright.
    tracker.handle(_button_event(THROTTLE, 27, 1))
    assert tracker.state.reverse is True
    assert tracker.state.throttle == pytest.approx(-0.50)

    tracker.handle(_button_event(THROTTLE, 26, 1))
    assert tracker.state.reverse is False
    assert tracker.state.throttle == pytest.approx(0.75)


def test_zero_preset_leaves_the_direction_alone():
    binds = _binds(
        reverse_is_hold=False,
        speed_presets=((0.0, ButtonBinding("231D3201", "Joy_10")),),
    )
    tracker = FlightTracker(resolve_mapping(binds, [STICK, THROTTLE]))
    tracker.handle(_button_event(THROTTLE, 4, 1))  # toggle into reverse
    tracker.handle(_button_event(THROTTLE, 9, 1))  # SetSpeedZero
    assert tracker.state.throttle == pytest.approx(0.0)
    assert tracker.state.reverse is True


def test_ahead_thrust_is_bidirectional_without_reverse():
    binds = _binds(
        axes={"ahead": AxisBinding("231D3201", "Joy_ZAxis", False, 0.0)},
        throttle_forward_only=False,
        reverse=None,
    )
    tracker = FlightTracker(resolve_mapping(binds, [STICK, THROTTLE]))
    tracker.handle(_axis_event(THROTTLE, 2, -32768))
    assert tracker.state.throttle == pytest.approx(-1.0)
    assert tracker.state.reverse is None


def test_full_range_throttle_uses_the_axis_directly():
    binds = _binds(throttle_forward_only=False, reverse=None, speed_presets=())
    tracker = FlightTracker(resolve_mapping(binds, [STICK, THROTTLE]))
    tracker.handle(_axis_event(THROTTLE, 1, -32768))
    assert tracker.state.throttle == pytest.approx(-1.0)
    assert tracker.state.reverse is None


def test_unbound_reverse_can_never_go_negative():
    binds = _binds(reverse=None, speed_presets=())
    tracker = FlightTracker(resolve_mapping(binds, [STICK, THROTTLE]))
    tracker.handle(_axis_event(THROTTLE, 1, -32768))
    assert tracker.state.reverse is False
    assert tracker.state.throttle == pytest.approx(0.0)


def test_unobservable_reverse_reports_unknown():
    # Bound to a keyboard: the pilot can reverse, we just cannot see it.
    binds = _binds(reverse=None, reverse_unobservable=True, speed_presets=())
    tracker = FlightTracker(resolve_mapping(binds, [STICK, THROTTLE]))
    tracker.handle(_axis_event(THROTTLE, 1, 32767))
    assert tracker.state.reverse is None


def test_events_from_unmapped_controls_are_ignored():
    tracker = FlightTracker(resolve_mapping(_binds(), [STICK, THROTTLE]))
    assert tracker.handle(_axis_event(STICK, 9, 32767)) is False
    assert tracker.handle(_button_event(STICK, 60, 1)) is False
    assert tracker.state == flight_axes.FlightState(reverse=False, throttle=0.5)


def test_repeated_axis_values_report_no_change():
    tracker = FlightTracker(resolve_mapping(_binds(), [STICK, THROTTLE]))
    assert tracker.handle(_axis_event(STICK, 0, 1000)) is True
    assert tracker.handle(_axis_event(STICK, 0, 1000)) is False


def test_reset_forgets_observed_state():
    tracker = FlightTracker(resolve_mapping(_binds(), [STICK, THROTTLE]))
    tracker.handle(_axis_event(STICK, 0, 32767))
    tracker.handle(_button_event(THROTTLE, 4, 1))
    tracker.reset()
    assert tracker.state.roll == 0.0
    assert tracker.state.reverse is False


def test_empty_mapping_is_inert():
    tracker = FlightTracker(FlightMapping())
    tracker.handle(_axis_event(STICK, 0, 32767))
    assert tracker.state == flight_axes.FlightState(reverse=False)


#  persistence


def test_mapping_round_trips_through_config():
    original = resolve_mapping(_binds(), [STICK, THROTTLE])
    restored = FlightMapping.from_config(original.to_config())
    assert restored.axes == original.axes
    assert restored.reverse == original.reverse
    assert restored.reverse_is_hold == original.reverse_is_hold
    assert restored.throttle_forward_only == original.throttle_forward_only
    assert restored.speed_presets == original.speed_presets


def test_restored_mapping_drives_the_tracker_identically():
    original = resolve_mapping(_binds(), [STICK, THROTTLE])
    restored = FlightMapping.from_config(original.to_config())
    events = [_axis_event(THROTTLE, 1, 32767), _button_event(THROTTLE, 4, 1)]
    trackers = [FlightTracker(original), FlightTracker(restored)]
    for tracker in trackers:
        for event in events:
            tracker.handle(event)
    assert trackers[0].state == trackers[1].state


def test_unresolved_reasons_are_not_persisted():
    # They describe one moment's hardware; recompute them, never restore them.
    mapping = resolve_mapping(_binds(), [STICK])  # throttle stick missing
    assert mapping.unresolved
    assert "unresolved" not in mapping.to_config()
    assert FlightMapping.from_config(mapping.to_config()).unresolved == ()


def test_mapping_from_junk_config_is_empty():
    for junk in (None, [], "nope", 42, {"axes": "not-a-mapping"}):
        assert FlightMapping.from_config(junk).is_empty


def test_mapping_drops_malformed_entries_but_keeps_good_ones():
    raw = {
        "axes": {
            "roll": {"device": "d", "index": 0},
            "pitch": {"device": "d", "index": -1},     # negative index
            "yaw": {"device": "", "index": 2},         # no device
            "lateral": {"device": "d", "index": True},  # bool is not an index
            "vertical": "garbage",
        },
        "reverse": {"device": "d", "index": 4},
        "speed_presets": [
            [0.75, {"device": "d", "index": 26}],
            [9.0, {"device": "d", "index": 1}],        # out of range
            ["x", {"device": "d", "index": 2}],        # unparseable value
            [0.5],                                     # wrong shape
        ],
    }
    mapping = FlightMapping.from_config(raw)
    assert set(mapping.axes) == {"roll"}
    assert mapping.reverse == ResolvedButton("d", 4)
    assert mapping.speed_presets == ((0.75, ResolvedButton("d", 26)),)


def test_deadzone_survives_and_is_clamped():
    raw = {"axes": {"roll": {"device": "d", "index": 0, "deadzone": -0.25}}}
    assert FlightMapping.from_config(raw).axes["roll"].deadzone == 0.25
    raw = {"axes": {"roll": {"device": "d", "index": 0, "deadzone": 5.0}}}
    assert FlightMapping.from_config(raw).axes["roll"].deadzone == 1.0
    raw = {"axes": {"roll": {"device": "d", "index": 0, "deadzone": "junk"}}}
    assert FlightMapping.from_config(raw).axes["roll"].deadzone == 0.0
