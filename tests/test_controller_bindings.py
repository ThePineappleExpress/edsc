from edsc.controller_bindings import (
    ControllerBinding,
    action_for_event,
    assign_binding,
    parse_bindings,
    serialize_bindings,
)
from edsc.platform.controller import ControllerEvent


def test_binding_config_round_trip_and_description():
    bindings = {
        "previous_tab": ControllerBinding("button", 7, 1),
        "refresh_search": ControllerBinding("hat", 1, 0x09),
    }

    payload = serialize_bindings(bindings)

    assert parse_bindings(payload) == bindings
    assert bindings["previous_tab"].describe() == "Button 7"
    assert bindings["refresh_search"].describe() == "Hat 1 Up + Left"


def test_parser_ignores_unknown_actions_and_malformed_values():
    assert parse_bindings(
        {
            "previous_tab": {"kind": "axis", "index": 0, "value": 32767},
            "next_tab": {"kind": "button", "index": True, "value": 1},
            "toggle_opacity": {"kind": "hat", "index": 0, "value": 0},
            "unknown": {"kind": "button", "index": 1, "value": 1},
        }
    ) == {}
    assert parse_bindings([]) == {}


def test_assign_binding_keeps_each_control_unique():
    control = ControllerBinding("button", 3, 1)
    bindings = {"previous_tab": control}

    assign_binding(bindings, "next_tab", control)

    assert bindings == {"next_tab": control}


def test_parser_keeps_only_the_first_action_for_duplicate_controls():
    control = {"kind": "button", "index": 3, "value": 1}

    assert parse_bindings(
        {"previous_tab": control, "next_tab": control}
    ) == {"previous_tab": ControllerBinding("button", 3, 1)}


def test_action_matching_uses_activation_edges_only():
    payload = {
        "next_tab": {"kind": "button", "index": 4, "value": 1},
        "refresh_search": {"kind": "hat", "index": 0, "value": 0x03},
    }

    assert action_for_event(
        payload, ControllerEvent("stick", "button", 4, 1)
    ) == "next_tab"
    assert action_for_event(
        payload, ControllerEvent("stick", "button", 4, 0)
    ) is None
    assert action_for_event(
        payload, ControllerEvent("stick", "button", 4, 1, initial=True)
    ) is None
    assert action_for_event(
        payload, ControllerEvent("stick", "hat", 0, 0x03)
    ) == "refresh_search"
    assert action_for_event(
        payload, ControllerEvent("stick", "hat", 0, 0x01)
    ) is None
    assert action_for_event(
        payload, ControllerEvent("stick", "hat", 0, 0)
    ) is None


def test_recording_excludes_analog_release_center_and_initial_events():
    assert ControllerBinding.from_event(
        ControllerEvent("stick", "button", 2, 1)
    ) == ControllerBinding("button", 2, 1)
    assert ControllerBinding.from_event(
        ControllerEvent("stick", "hat", 1, 0x06)
    ) == ControllerBinding("hat", 1, 0x06)

    ignored = (
        ControllerEvent("stick", "axis", 0, 32767),
        ControllerEvent("stick", "ball_x", 0, 5),
        ControllerEvent("stick", "button", 2, 0),
        ControllerEvent("stick", "hat", 1, 0),
        ControllerEvent("stick", "button", 2, 1, initial=True),
        ControllerEvent("stick", "button", -1, 1),
    )
    assert all(ControllerBinding.from_event(event) is None for event in ignored)
