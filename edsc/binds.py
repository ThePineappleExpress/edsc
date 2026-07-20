"""Read Elite's flight-control bindings from its active preset; reports only what the preset files say -- turning a ``Joy_RZAxis`` name into a live controller axis is the resolver's job. Frontier's files are never written here (import is strictly read-only)."""

# SPDX-License-Identifier: GPL-3.0-or-later

from __future__ import annotations

import os
import re
import sys
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from pathlib import Path

from . import ELITE_STEAM_APPID
from .journal.locator import steam_library_folders

_BINDINGS_TAIL = (
    Path("Frontier Developments") / "Elite Dangerous" / "Options" / "Bindings"
)
_PROTON_LOCALAPPDATA = (
    Path("pfx") / "drive_c" / "users" / "steamuser" / "AppData" / "Local"
)
_START_PRESET_GLOB = "StartPreset.*.start"
_START_PRESET_RE = re.compile(r"StartPreset\.(\d+)\.start$")
_BINDS_MINOR_RE = re.compile(r"\.(\d+)\.(\d+)\.binds$")

# Elite writes the throttle range as an empty string for full range; forward-only is the only positive value it emits, so treating "anything else" as full range keeps us correct if Frontier ever adds a spelling for the default.
_FORWARD_ONLY = "Bindings_ThrottleForewardOnly"

# Device attribute values that never denote a joystick.
_NON_JOYSTICK = frozenset({"", "{NoDevice}", "Keyboard", "Mouse"})
# Catch-all device names with no fixed hardware behind them: DeviceMappings.xml can't resolve these, so bindings naming them fall back to a manual mapping.
_WILDCARD_DEVICES = frozenset({"GenericJoystick", "XB360 Pad"})

_VID_PID_RE = re.compile(r"^[0-9A-Fa-f]{8}$")

# Our six degrees of freedom, mapped to the preset tags that carry them; ``ahead`` is Elite's bidirectional raw thrust, and when it's bound the throttle needs no reverse handling.
AXIS_TAGS: dict[str, str] = {
    "roll": "RollAxisRaw",
    "pitch": "PitchAxisRaw",
    "yaw": "YawAxisRaw",
    "lateral": "LateralThrustRaw",
    "vertical": "VerticalThrustRaw",
    "throttle": "ThrottleAxis",
    "ahead": "AheadThrust",
}

# Absolute throttle presets, each an unambiguous statement of both value and direction -- the only way to resynchronise a held/toggled reverse state.
SPEED_PRESET_TAGS: tuple[tuple[str, float], ...] = (
    ("SetSpeedMinus100", -1.00),
    ("SetSpeedMinus75", -0.75),
    ("SetSpeedMinus50", -0.50),
    ("SetSpeedMinus25", -0.25),
    ("SetSpeedZero", 0.00),
    ("SetSpeed25", 0.25),
    ("SetSpeed50", 0.50),
    ("SetSpeed75", 0.75),
    ("SetSpeed100", 1.00),
)


@dataclass(frozen=True, slots=True)
class AxisBinding:
    """One axis bound to a device, as the preset states it."""

    device: str
    key: str
    inverted: bool = False
    deadzone: float = 0.0


@dataclass(frozen=True, slots=True)
class ButtonBinding:
    """One button bound to a device, as the preset states it."""

    device: str
    key: str


@dataclass(frozen=True, slots=True)
class FlightBinds:
    """Everything a flight gizmo needs from one preset."""

    preset: str = ""
    source: Path | None = None
    axes: dict[str, AxisBinding] = field(default_factory=dict)
    throttle_forward_only: bool = False
    reverse: ButtonBinding | None = None
    # Elite's ``ToggleOn`` flag: false means hold-to-reverse, which is directly observable and needs no state tracking.
    reverse_is_hold: bool = False
    # Reverse is bound, but to a keyboard/mouse we never watch; distinct from unbound -- the pilot *can* reverse, we simply can't see when.
    reverse_unobservable: bool = False
    speed_presets: tuple[tuple[float, ButtonBinding], ...] = ()
    # Engine boost (``UseBoostJuice``): a button, watched to time the cooldown.
    boost: ButtonBinding | None = None

    @property
    def devices(self) -> tuple[str, ...]:
        """Every distinct device named by a binding, in first-seen order."""
        seen: list[str] = []
        for binding in (
            *self.axes.values(),
            *(b for _value, b in self.speed_presets),
            *((self.reverse,) if self.reverse is not None else ()),
            *((self.boost,) if self.boost is not None else ()),
        ):
            if binding.device not in seen:
                seen.append(binding.device)
        return tuple(seen)


def is_joystick_device(device: str) -> bool:
    """Whether ``device`` names a joystick we could observe."""
    return device not in _NON_JOYSTICK


def resolve_device(
    device: str, mappings: dict[str, tuple[str, ...]]
) -> tuple[str, ...]:
    """Candidate ``VVVVPPPP`` ids for one ``Device`` attribute value; custom presets name devices by vendor+product hex directly, while Frontier's shipped schemes use symbolic names that ``DeviceMappings.xml`` expands (often to several hardware variants). Wildcards and non-joysticks resolve to nothing."""
    if not is_joystick_device(device) or device in _WILDCARD_DEVICES:
        return ()
    if _VID_PID_RE.match(device):
        return (device.upper(),)
    return mappings.get(device, ())


def _text_value(parent: ET.Element, tag: str) -> str:
    element = parent.find(tag)
    return (element.get("Value") or "").strip() if element is not None else ""


def _flag(parent: ET.Element, tag: str) -> bool:
    return _text_value(parent, tag) == "1"


def _deadzone(parent: ET.Element) -> float:
    try:
        return abs(float(_text_value(parent, "Deadzone") or 0.0))
    except ValueError:
        return 0.0


def _parse_axis(root: ET.Element, tag: str) -> AxisBinding | None:
    element = root.find(tag)
    if element is None:
        return None
    binding = element.find("Binding")
    if binding is None:
        return None
    device = (binding.get("Device") or "").strip()
    key = (binding.get("Key") or "").strip()
    if not key or not is_joystick_device(device):
        return None
    return AxisBinding(device, key, _flag(element, "Inverted"), _deadzone(element))


def _parse_button(root: ET.Element, tag: str) -> ButtonBinding | None:
    """First joystick-bound of ``Primary``/``Secondary``, if either is one."""
    element = root.find(tag)
    if element is None:
        return None
    for slot in ("Primary", "Secondary"):
        bound = element.find(slot)
        if bound is None:
            continue
        device = (bound.get("Device") or "").strip()
        key = (bound.get("Key") or "").strip()
        if key and is_joystick_device(device):
            return ButtonBinding(device, key)
    return None


def _bound_to_anything(root: ET.Element, tag: str) -> bool:
    """Whether ``tag`` names any device at all, joystick or not."""
    element = root.find(tag)
    if element is None:
        return False
    for slot in ("Primary", "Secondary"):
        bound = element.find(slot)
        if bound is None:
            continue
        if (bound.get("Key") or "").strip() and (
            bound.get("Device") or ""
        ).strip() not in ("", "{NoDevice}"):
            return True
    return False


def parse_device_mappings(path: Path) -> dict[str, tuple[str, ...]]:
    """Symbolic device name -> ``VVVVPPPP`` ids; ``{}`` if unreadable."""
    try:
        root = ET.parse(path).getroot()
    except (OSError, ET.ParseError):
        return {}
    mappings: dict[str, tuple[str, ...]] = {}
    for device in root:
        ids: list[str] = []
        for source in (device, *device.findall("Alternative")):
            vendor = (source.findtext("VID") or "").strip()
            product = (source.findtext("PID") or "").strip()
            if vendor and product:
                identifier = f"{vendor}{product}".upper()
                if identifier not in ids:
                    ids.append(identifier)
        if ids:
            mappings[device.tag] = tuple(ids)
    return mappings


def parse_binds(path: Path) -> FlightBinds | None:
    """Parse one preset file, or ``None`` when it is unreadable or not a preset."""
    try:
        root = ET.parse(path).getroot()
    except (OSError, ET.ParseError):
        return None
    if root.tag != "Root":
        return None

    axes = {
        name: binding
        for name, tag in AXIS_TAGS.items()
        if (binding := _parse_axis(root, tag)) is not None
    }
    reverse_element = root.find("ToggleReverseThrottleInput")
    reverse = _parse_button(root, "ToggleReverseThrottleInput")
    presets = tuple(
        (value, binding)
        for tag, value in SPEED_PRESET_TAGS
        if (binding := _parse_button(root, tag)) is not None
    )
    return FlightBinds(
        preset=(root.get("PresetName") or path.stem).strip(),
        source=path,
        axes=axes,
        throttle_forward_only=_text_value(root, "ThrottleRange") == _FORWARD_ONLY,
        reverse=reverse,
        reverse_is_hold=(
            reverse_element is not None and not _flag(reverse_element, "ToggleOn")
        ),
        reverse_unobservable=(
            reverse is None
            and _bound_to_anything(root, "ToggleReverseThrottleInput")
        ),
        speed_presets=presets,
        boost=_parse_button(root, "UseBoostJuice"),
    )


def _bindings_dir_candidates(journal_dir: Path | None = None) -> list[Path]:
    """Ordered ``Options/Bindings`` candidates for this OS."""
    candidates: list[Path] = []
    # The journal dir shares a user profile with the options dir, so walking its parents also finds nonstandard installs.
    if journal_dir is not None:
        for parent in Path(journal_dir).parents:
            candidates.append(parent / "AppData" / "Local" / _BINDINGS_TAIL)
    if sys.platform == "win32":
        base = os.environ.get("LOCALAPPDATA") or (Path.home() / "AppData" / "Local")
        candidates.append(Path(base) / _BINDINGS_TAIL)
    else:
        for lib in steam_library_folders():
            prefix = lib / "steamapps" / "compatdata" / ELITE_STEAM_APPID
            candidates.append(prefix / _PROTON_LOCALAPPDATA / _BINDINGS_TAIL)
    return candidates


def _control_scheme_dirs() -> list[Path]:
    """``ControlSchemes`` directories inside Steam installs of the game."""
    directories: list[Path] = []
    for lib in steam_library_folders():
        products = lib / "steamapps" / "common" / "Elite Dangerous" / "Products"
        try:
            versions = list(products.iterdir())
        except OSError:
            continue
        # Prefer the live (Odyssey) build over legacy Horizons product dirs.
        versions.sort(key=lambda p: (0 if "odyssey" in p.name.lower() else 1, p.name))
        for version in versions:
            schemes = version / "ControlSchemes"
            if schemes.is_dir():
                directories.append(schemes)
    return directories


def find_bindings_dir(journal_dir: Path | None = None) -> Path | None:
    """The user's ``Options/Bindings`` directory, if one exists."""
    for candidate in _bindings_dir_candidates(journal_dir):
        if candidate.is_dir():
            return candidate
    return None


def start_preset(bindings_dir: Path) -> str:
    """The ship preset named by the highest-versioned ``StartPreset``; the file's first line is the ship bindings preset, later lines cover other contexts we don't read."""
    best: tuple[int, Path] | None = None
    try:
        starts = list(bindings_dir.glob(_START_PRESET_GLOB))
    except OSError:
        return ""
    for path in starts:
        match = _START_PRESET_RE.search(path.name)
        if match is None:
            continue
        major = int(match.group(1))
        if best is None or major > best[0]:
            best = (major, path)
    if best is None:
        return ""
    try:
        lines = best[1].read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return ""
    return lines[0].strip() if lines else ""


def find_preset_file(
    preset: str,
    bindings_dir: Path | None,
    scheme_dirs: list[Path] | None = None,
) -> Path | None:
    """Locate ``preset``: a user custom first, else a scheme Frontier ships; customs are versioned ``<name>.<major>.<minor>.binds`` (newest minor wins), shipped schemes are plain ``<name>.binds`` in the game install."""
    if not preset:
        return None
    if bindings_dir is not None:
        best: tuple[tuple[int, int], Path] | None = None
        try:
            candidates = list(bindings_dir.glob(f"{preset}.*.binds"))
        except OSError:
            candidates = []
        for path in candidates:
            match = _BINDS_MINOR_RE.search(path.name)
            if match is None:
                continue
            version = (int(match.group(1)), int(match.group(2)))
            if best is None or version > best[0]:
                best = (version, path)
        if best is not None:
            return best[1]
    for schemes in scheme_dirs if scheme_dirs is not None else _control_scheme_dirs():
        path = schemes / f"{preset}.binds"
        if path.is_file():
            return path
    return None


def load_device_mappings(
    scheme_dirs: list[Path] | None = None,
) -> dict[str, tuple[str, ...]]:
    """Symbolic device names from the first readable ``DeviceMappings.xml``."""
    for schemes in scheme_dirs if scheme_dirs is not None else _control_scheme_dirs():
        mappings = parse_device_mappings(schemes / "DeviceMappings.xml")
        if mappings:
            return mappings
    return {}


def load_binds(journal_dir: Path | None = None) -> FlightBinds | None:
    """Best-effort read of the active flight bindings, or ``None``; ``$EDSC_BINDS`` names a preset file directly, bypassing preset resolution."""
    override = os.environ.get("EDSC_BINDS")
    if override:
        return parse_binds(Path(override).expanduser())

    bindings_dir = find_bindings_dir(journal_dir)
    if bindings_dir is None:
        return None
    path = find_preset_file(start_preset(bindings_dir), bindings_dir)
    return parse_binds(path) if path is not None else None
