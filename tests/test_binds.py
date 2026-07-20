from pathlib import Path

from edsc import binds

# Shaped after a real Custom.4.2.binds: devices named by vendor+product hex, a forward-only throttle with hold-to-reverse, presets split across a stick and a throttle.
CUSTOM_PRESET = """<?xml version="1.0" encoding="UTF-8" ?>
<Root PresetName="Custom" MajorVersion="4" MinorVersion="2">
  <RollAxisRaw>
    <Binding Device="231D0200" Key="Joy_XAxis" />
    <Inverted Value="0" />
    <Deadzone Value="0.00000000" />
  </RollAxisRaw>
  <PitchAxisRaw>
    <Binding Device="231D0200" Key="Joy_YAxis" />
    <Inverted Value="1" />
    <Deadzone Value="0.12500000" />
  </PitchAxisRaw>
  <YawAxisRaw>
    <Binding Device="231D0200" Key="Joy_RZAxis" />
    <Inverted Value="1" />
    <Deadzone Value="0.00000000" />
  </YawAxisRaw>
  <LateralThrustRaw>
    <Binding Device="231D3201" Key="Joy_XAxis" />
    <Inverted Value="1" />
    <Deadzone Value="0.00000000" />
  </LateralThrustRaw>
  <AheadThrust>
    <Binding Device="{NoDevice}" Key="" />
    <Inverted Value="0" />
    <Deadzone Value="0.00000000" />
  </AheadThrust>
  <ThrottleAxis>
    <Binding Device="231D3201" Key="Joy_YAxis" />
    <Inverted Value="0" />
    <Deadzone Value="0.00000000" />
  </ThrottleAxis>
  <ThrottleRange Value="Bindings_ThrottleForewardOnly" />
  <ToggleReverseThrottleInput>
    <Primary Device="231D3201" Key="Joy_5" />
    <Secondary Device="{NoDevice}" Key="" />
    <ToggleOn Value="0" />
  </ToggleReverseThrottleInput>
  <SetSpeedZero>
    <Primary Device="Keyboard" Key="Key_X" />
    <Secondary Device="{NoDevice}" Key="" />
  </SetSpeedZero>
  <SetSpeed75>
    <Primary Device="231D3201" Key="Joy_27" />
    <Secondary Device="{NoDevice}" Key="" />
  </SetSpeed75>
  <SetSpeedMinus50>
    <Primary Device="{NoDevice}" Key="" />
    <Secondary Device="231D3201" Key="Joy_28" />
  </SetSpeedMinus50>
</Root>
"""

# Shaped after a shipped scheme: symbolic device names and a toggled reverse.
STOCK_PRESET = """<?xml version="1.0" encoding="UTF-8" ?>
<Root PresetName="SaitekX52" MajorVersion="4" MinorVersion="0">
  <RollAxisRaw>
    <Binding Device="SaitekX52" Key="Joy_XAxis" />
    <Inverted Value="0" />
    <Deadzone Value="0.00000000" />
  </RollAxisRaw>
  <ThrottleAxis>
    <Binding Device="SaitekX52" Key="Joy_ZAxis" />
    <Inverted Value="1" />
    <Deadzone Value="0.00000000" />
  </ThrottleAxis>
  <ThrottleRange Value="Bindings_ThrottleForewardOnly" />
  <ToggleReverseThrottleInput>
    <Primary Device="SaitekX52" Key="Joy_7" />
    <Secondary Device="{NoDevice}" Key="" />
    <ToggleOn Value="1" />
  </ToggleReverseThrottleInput>
</Root>
"""

# Full range is an empty value, not a spelling of its own.
FULL_RANGE_PRESET = """<?xml version="1.0" encoding="UTF-8" ?>
<Root PresetName="Full" MajorVersion="4" MinorVersion="0">
  <AheadThrust>
    <Binding Device="044F0404" Key="Joy_ZAxis" />
    <Inverted Value="0" />
    <Deadzone Value="0.00000000" />
  </AheadThrust>
  <ThrottleRange Value="" />
  <ToggleReverseThrottleInput>
    <Primary Device="{NoDevice}" Key="" />
    <Secondary Device="{NoDevice}" Key="" />
    <ToggleOn Value="1" />
  </ToggleReverseThrottleInput>
</Root>
"""

DEVICE_MAPPINGS = """<?xml version="1.0" encoding="UTF-8" ?>
<Root>
  <SaitekX52>
    <PID>075C</PID><VID>06A3</VID>
    <Alternative><PID>0255</PID><VID>06A3</VID></Alternative>
  </SaitekX52>
  <ThrustMasterWarthogThrottle>
    <PID>0404</PID><VID>044f</VID>
  </ThrustMasterWarthogThrottle>
  <GenericJoystick>
  </GenericJoystick>
</Root>
"""


def _no_steam(monkeypatch):
    """Keep tests hermetic: never probe this machine's real Steam installs."""
    monkeypatch.setattr(binds, "steam_library_folders", list)
    monkeypatch.delenv("EDSC_BINDS", raising=False)


def _write(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


#  parsing


def test_parses_axes_with_inversion_and_deadzone(tmp_path):
    result = binds.parse_binds(_write(tmp_path / "Custom.4.2.binds", CUSTOM_PRESET))
    assert result.preset == "Custom"
    assert result.axes["roll"] == binds.AxisBinding("231D0200", "Joy_XAxis", False, 0.0)
    assert result.axes["pitch"] == binds.AxisBinding(
        "231D0200", "Joy_YAxis", True, 0.125
    )
    assert result.axes["throttle"].device == "231D3201"


def test_unbound_axis_is_absent(tmp_path):
    result = binds.parse_binds(_write(tmp_path / "Custom.4.2.binds", CUSTOM_PRESET))
    # AheadThrust is {NoDevice} here; vertical thrust is not in the file at all.
    assert "ahead" not in result.axes
    assert "vertical" not in result.axes


def test_forward_only_and_full_range(tmp_path):
    forward = binds.parse_binds(_write(tmp_path / "a.4.2.binds", CUSTOM_PRESET))
    full = binds.parse_binds(_write(tmp_path / "b.4.0.binds", FULL_RANGE_PRESET))
    assert forward.throttle_forward_only is True
    assert full.throttle_forward_only is False


def test_reverse_hold_versus_toggle(tmp_path):
    hold = binds.parse_binds(_write(tmp_path / "a.4.2.binds", CUSTOM_PRESET))
    toggle = binds.parse_binds(_write(tmp_path / "b.4.0.binds", STOCK_PRESET))
    assert hold.reverse == binds.ButtonBinding("231D3201", "Joy_5")
    assert hold.reverse_is_hold is True
    assert toggle.reverse_is_hold is False


def test_unbound_reverse_is_none(tmp_path):
    result = binds.parse_binds(_write(tmp_path / "b.4.0.binds", FULL_RANGE_PRESET))
    assert result.reverse is None


def test_speed_presets_skip_non_joysticks_and_use_secondary(tmp_path):
    result = binds.parse_binds(_write(tmp_path / "Custom.4.2.binds", CUSTOM_PRESET))
    # SetSpeedZero is keyboard-bound/unobservable; SetSpeedMinus50 is on Secondary only; presets stay in Elite's declaration order.
    assert result.speed_presets == (
        (-0.50, binds.ButtonBinding("231D3201", "Joy_28")),
        (0.75, binds.ButtonBinding("231D3201", "Joy_27")),
    )


def test_devices_lists_every_bound_device(tmp_path):
    result = binds.parse_binds(_write(tmp_path / "Custom.4.2.binds", CUSTOM_PRESET))
    assert result.devices == ("231D0200", "231D3201")


def test_unreadable_and_foreign_files_return_none(tmp_path):
    assert binds.parse_binds(tmp_path / "missing.binds") is None
    assert binds.parse_binds(_write(tmp_path / "bad.binds", "<Root><oops")) is None
    assert binds.parse_binds(_write(tmp_path / "other.binds", "<NotAPreset />")) is None


#  device resolution


def test_device_mappings_expand_alternatives(tmp_path):
    mappings = binds.parse_device_mappings(
        _write(tmp_path / "DeviceMappings.xml", DEVICE_MAPPINGS)
    )
    assert mappings["SaitekX52"] == ("06A3075C", "06A30255")
    assert mappings["ThrustMasterWarthogThrottle"] == ("044F0404",)
    # A name with no ids at all is not a usable mapping.
    assert "GenericJoystick" not in mappings


def test_device_mappings_unreadable_is_empty(tmp_path):
    assert binds.parse_device_mappings(tmp_path / "missing.xml") == {}


def test_resolve_device_handles_every_form(tmp_path):
    mappings = binds.parse_device_mappings(
        _write(tmp_path / "DeviceMappings.xml", DEVICE_MAPPINGS)
    )
    assert binds.resolve_device("231D3201", mappings) == ("231D3201",)
    assert binds.resolve_device("231d3201", mappings) == ("231D3201",)
    assert binds.resolve_device("SaitekX52", mappings) == ("06A3075C", "06A30255")
    # Wildcards and non-joysticks have no fixed hardware behind them.
    assert binds.resolve_device("GenericJoystick", mappings) == ()
    assert binds.resolve_device("XB360 Pad", mappings) == ()
    assert binds.resolve_device("Keyboard", mappings) == ()
    assert binds.resolve_device("{NoDevice}", mappings) == ()
    assert binds.resolve_device("NeverHeardOfIt", mappings) == ()


#  preset resolution


def test_start_preset_reads_first_line_of_highest_major(tmp_path):
    _write(tmp_path / "StartPreset.3.start", "Older\nOlder")
    _write(tmp_path / "StartPreset.4.start", "Custom\nCustom\nKeyboardMouseOnly")
    assert binds.start_preset(tmp_path) == "Custom"


def test_start_preset_missing_is_empty(tmp_path):
    assert binds.start_preset(tmp_path) == ""


def test_find_preset_file_prefers_highest_minor(tmp_path):
    _write(tmp_path / "Custom.4.2.binds", CUSTOM_PRESET)
    _write(tmp_path / "Custom.4.10.binds", CUSTOM_PRESET)
    _write(tmp_path / "Custom.4.9.binds", CUSTOM_PRESET)
    found = binds.find_preset_file("Custom", tmp_path, [])
    assert found.name == "Custom.4.10.binds"


def test_find_preset_file_ignores_backups(tmp_path):
    _write(tmp_path / "Custom.4.2.binds", CUSTOM_PRESET)
    _write(tmp_path / "Custom.4.2.binds.1065974309.backup", CUSTOM_PRESET)
    assert binds.find_preset_file("Custom", tmp_path, []).name == "Custom.4.2.binds"


def test_find_preset_file_falls_back_to_shipped_scheme(tmp_path):
    bindings = tmp_path / "Bindings"
    schemes = tmp_path / "ControlSchemes"
    _write(schemes / "SaitekX52.binds", STOCK_PRESET)
    bindings.mkdir()
    found = binds.find_preset_file("SaitekX52", bindings, [schemes])
    assert found.name == "SaitekX52.binds"


def test_find_preset_file_unknown_preset(tmp_path):
    assert binds.find_preset_file("", tmp_path, []) is None
    assert binds.find_preset_file("Nope", tmp_path, []) is None


#  loading


def test_load_binds_env_override_wins(tmp_path, monkeypatch):
    _no_steam(monkeypatch)
    path = _write(tmp_path / "anywhere.binds", CUSTOM_PRESET)
    monkeypatch.setenv("EDSC_BINDS", str(path))
    result = binds.load_binds()
    assert result.preset == "Custom"
    assert result.source == path


def test_load_binds_end_to_end_from_journal_dir(tmp_path, monkeypatch):
    _no_steam(monkeypatch)
    # Options and Saved Games share a user profile, as they do under Proton.
    profile = tmp_path / "users" / "steamuser"
    journal = profile / "Saved Games" / "Frontier Developments" / "Elite Dangerous"
    journal.mkdir(parents=True)
    bindings = profile / "AppData" / "Local" / binds._BINDINGS_TAIL
    _write(bindings / "StartPreset.4.start", "Custom\nCustom")
    _write(bindings / "Custom.4.2.binds", CUSTOM_PRESET)

    result = binds.load_binds(journal)
    assert result.preset == "Custom"
    assert result.axes["yaw"].key == "Joy_RZAxis"
    assert result.reverse_is_hold is True


def test_load_binds_without_a_bindings_dir(tmp_path, monkeypatch):
    _no_steam(monkeypatch)
    assert binds.load_binds(tmp_path) is None
