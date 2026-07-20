from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from PySide6.QtCore import Qt
from PySide6.QtTest import QSignalSpy
from PySide6.QtWidgets import QLabel, QVBoxLayout, QWidget

from edsc.config import Config
from edsc.engine import Engine
from edsc.gui import app as app_module, thargoid_effects as effects_module
from edsc.gui.app import Application
from edsc.gui.thargoid_effects import ThargoidEffectController
from edsc.thargoids import ThargoidDetector, ThargoidEvidence

ORIGIN = {"StarSystem": "Pleiades Sector EB-X c1-16", "SystemAddress": 4481764725386}
DESTINATION = {"StarSystem": "Atlas", "SystemAddress": 254709589156}


def _event(event_type, **values):
    return {"timestamp": "2026-07-14T12:00:00Z", "event": event_type, **values}


def _seeded_detector():
    detector = ThargoidDetector()
    detector.process(_event("Location", **ORIGIN))
    return detector


def _target(qapp):
    target = QWidget()
    target.resize(420, 260)
    layout = QVBoxLayout(target)
    layout.addWidget(QLabel("EDSC live interface"))
    target.show()
    qapp.processEvents()
    return target


def _hyperdict(detector):
    detector.process(_event("StartJump", JumpType="Hyperspace", **DESTINATION))
    return detector.process(_event("FSDJump", **ORIGIN))


def test_hyperdiction_is_destination_mismatch_returning_to_origin():
    detector = _seeded_detector()

    evidence = _hyperdict(detector)

    assert evidence is ThargoidEvidence.HYPERDICTION
    assert detector.encounter_active
    assert detector.current_system.address == ORIGIN["SystemAddress"]


def test_successful_jump_does_not_false_positive_and_ends_an_encounter():
    detector = _seeded_detector()
    assert _hyperdict(detector) is ThargoidEvidence.HYPERDICTION

    detector.process(_event("StartJump", JumpType="Hyperspace", **DESTINATION))
    evidence = detector.process(_event("FSDJump", **DESTINATION))

    assert evidence is ThargoidEvidence.ENCOUNTER_ENDED
    assert not detector.encounter_active


def test_supercruise_start_cannot_be_reused_as_hyperdiction_evidence():
    detector = _seeded_detector()

    detector.process(_event("StartJump", JumpType="Supercruise"))
    evidence = detector.process(_event("FSDJump", **ORIGIN))

    assert evidence is None
    assert not detector.encounter_active


@pytest.mark.parametrize(
    "drop",
    [
        _event(
            "USSDrop",
            USSType="$USS_Type_NonHuman;",
            USSType_Localised="Nonhuman signal source",
            USSThreat=7,
        ),
        _event(
            "SupercruiseDestinationDrop",
            Type="$USS_Type_NonHuman;",
            Type_Localised="Non-Human Signal Source",
            Threat=7,
        ),
    ],
)
def test_nonhuman_signal_source_needs_an_enemy_before_confirmation(drop):
    detector = _seeded_detector()

    assert detector.process(drop) is None
    assert detector.in_nonhuman_instance
    assert not detector.encounter_active
    assert detector.process(_event("UnderAttack", Target="You")) is (
        ThargoidEvidence.HOSTILE_NONHUMAN_SIGNAL
    )
    assert detector.encounter_active


@pytest.mark.parametrize("music_track", ["Unknown_Encounter", "Combat_Unknown"])
def test_unknown_music_is_enemy_confirmation_inside_nonhuman_signal(music_track):
    detector = _seeded_detector()
    detector.process(
        _event("USSDrop", USSType="$USS_Type_NonHuman;", USSThreat=8)
    )

    evidence = detector.process(_event("Music", MusicTrack=music_track))

    assert evidence is ThargoidEvidence.HOSTILE_NONHUMAN_SIGNAL


def test_discovery_or_attack_outside_nonhuman_instance_does_not_confirm():
    detector = _seeded_detector()

    discovered = detector.process(
        _event(
            "FSSSignalDiscovered",
            USSType="$USS_Type_NonHuman;",
            ThreatLevel=8,
        )
    )
    attacked = detector.process(_event("UnderAttack", Target="You"))

    assert discovered is None
    assert attacked is None
    assert not detector.encounter_active


def test_supercruise_entry_ends_hostile_nonhuman_instance():
    detector = _seeded_detector()
    detector.process(_event("USSDrop", USSType="$USS_Type_NonHuman;"))
    detector.process(_event("UnderAttack", Target="You"))

    evidence = detector.process(_event("SupercruiseEntry", **ORIGIN))

    assert evidence is ThargoidEvidence.ENCOUNTER_ENDED
    assert not detector.in_nonhuman_instance
    assert not detector.encounter_active


def test_direct_journal_evidence_is_recognised_conservatively():
    detector = _seeded_detector()

    assert detector.process(_event("Interdicted", IsThargoid=False)) is None
    assert detector.process(_event("Interdicted", IsThargoid=True)) is (
        ThargoidEvidence.INTERDICTION
    )

    detector.reset(ORIGIN["StarSystem"])
    evidence = detector.process(
        _event(
            "FactionKillBond",
            VictimFaction="$faction_Thargoid;",
            VictimFaction_Localised="Thargoids",
        )
    )
    assert evidence is ThargoidEvidence.KILL_BOND

    detector.reset(ORIGIN["StarSystem"])
    assert detector.process(
        _event(
            "FactionKillBond",
            VictimFaction="$faction_Federation;",
            VictimFaction_Localised="Federation",
        )
    ) is None


def test_systems_shutdown_is_direct_evidence_even_without_prior_context():
    detector = _seeded_detector()

    evidence = detector.process(_event("SystemsShutdown"))

    assert evidence is ThargoidEvidence.SYSTEMS_SHUTDOWN
    assert detector.encounter_active


def test_effect_controller_glitches_then_blacks_out_and_recovers(qapp, monkeypatch):
    now = [100.0]
    monkeypatch.setattr(effects_module.time, "monotonic", lambda: now[0])
    target = _target(qapp)
    controller = ThargoidEffectController(target)
    controller.reset(ORIGIN["StarSystem"])
    evidence = QSignalSpy(controller.evidence_detected)

    controller.handle_event(
        _event("StartJump", JumpType="Hyperspace", **DESTINATION)
    )
    controller.handle_event(_event("FSDJump", StarSystem=ORIGIN["StarSystem"]))

    (effect,) = controller.effects
    assert evidence.count() == 1
    assert evidence.at(0)[0] is ThargoidEvidence.HYPERDICTION
    assert controller.phase == "glitch"
    assert effect.isVisible()
    assert effect.testAttribute(Qt.WA_TransparentForMouseEvents)
    assert controller._timer.isActive()

    controller.handle_event(_event("SystemsShutdown"))
    assert evidence.count() == 2
    assert controller.phase == "blackout"
    assert effect.phase == "blackout"

    now[0] += controller.BLACKOUT_SECONDS + 0.001
    controller._tick()
    assert controller.phase == "glitch"
    assert effect.phase == "glitch"

    controller.handle_event(_event("SupercruiseEntry", **ORIGIN))
    assert controller.phase == "idle"
    assert not effect.isVisible()
    assert not controller._timer.isActive()

    controller.reset()
    target.close()
    target.deleteLater()
    qapp.processEvents()


def test_new_target_joins_an_effect_already_in_progress(qapp):
    primary = _target(qapp)
    secondary = _target(qapp)
    controller = ThargoidEffectController(primary)
    controller.reset(ORIGIN["StarSystem"])
    _hyperdict(controller.detector)
    controller._start_glitch()

    secondary_effect = controller.add_target(secondary)

    assert secondary_effect.isVisible()
    assert secondary_effect.phase == "glitch"
    controller.remove_target(secondary)
    assert secondary_effect not in controller.effects

    controller.reset()
    for target in (primary, secondary):
        target.close()
        target.deleteLater()
    qapp.processEvents()


def test_engine_emits_unhandled_journal_events_on_live_only_surface(qapp):
    engine = Engine(Config())
    received = []
    engine.live_event.connect(received.append)

    event = _event("SystemsShutdown")
    engine._on_event(event)

    assert received == [event]
    assert engine.state.last_event_time == event["timestamp"]
    engine.deleteLater()
    qapp.processEvents()


def test_journal_ready_seeds_detector_from_replayed_app_state():
    application = object.__new__(Application)
    application.engine = SimpleNamespace(
        state=SimpleNamespace(
            current_system=ORIGIN["StarSystem"], docked_market_id=None
        ),
        journal_dir=None,
    )
    application.thargoid_effects = Mock()
    application.gizmos = Mock()

    application._on_journal_ready()

    application.thargoid_effects.reset.assert_called_once_with(
        ORIGIN["StarSystem"]
    )


def test_open_settings_attaches_and_removes_global_effect_target(
    qapp, monkeypatch
):
    dialogs = []

    class FakeDialog(QWidget):
        def __init__(self, *_args, **_kwargs):
            super().__init__()
            dialogs.append(self)

        def exec(self):
            return False

    application = object.__new__(Application)
    application.config = Config()
    application.overlay = QWidget()
    application.controllers = object()
    application.thargoid_effects = Mock()
    monkeypatch.setattr(app_module, "SettingsDialog", FakeDialog)

    application.open_settings()

    (dialog,) = dialogs
    application.thargoid_effects.add_target.assert_called_once_with(dialog)
    application.thargoid_effects.remove_target.assert_called_once_with(dialog)
    dialog.deleteLater()
    application.overlay.deleteLater()
    qapp.processEvents()
