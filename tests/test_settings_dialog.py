from types import SimpleNamespace
from unittest.mock import Mock, call

import pytest

from edsc import config as config_module
from edsc.config import Config
from edsc.eddn import EddnSender, EddnUplink
from edsc.flight_state import FlightStateTracker
from edsc.gui import app as app_module
from edsc.gui.app import Application
from edsc.gui.settings_dialog import SettingsDialog
from edsc.platform.controller import ControllerEvent


def test_docked_opacity_control_requires_automatic_mode(qapp):
    dialog = SettingsDialog(
        Config(
            overlay_opacity=0.82,
            auto_opacity_on_dock=False,
            docked_opacity=0.43,
        )
    )

    assert dialog.opacity.value() == 82
    assert dialog.docked_opacity.value() == 43
    assert not dialog.docked_opacity_wrap.isEnabled()

    dialog.auto_opacity_on_dock.setChecked(True)
    assert dialog.docked_opacity_wrap.isEnabled()

    dialog.opacity.setValue(76)
    dialog.docked_opacity.setValue(38)
    applied = Config()
    dialog.apply_to(applied)

    assert applied.overlay_opacity == 0.76
    assert applied.auto_opacity_on_dock is True
    assert applied.docked_opacity == 0.38
    dialog.deleteLater()
    qapp.processEvents()


def test_automatic_collapse_setting_is_applied(qapp):
    dialog = SettingsDialog(Config(auto_collapse_on_undock=True))

    assert dialog.auto_collapse_on_undock.isChecked()

    dialog.auto_collapse_on_undock.setChecked(False)
    applied = Config()
    dialog.apply_to(applied)

    assert applied.auto_collapse_on_undock is False
    dialog.deleteLater()
    qapp.processEvents()


def test_enabling_eddn_mints_anonymous_uploader_id(qapp):
    dialog = SettingsDialog(Config(eddn_enabled=None, eddn_uploader_id=""))
    assert not dialog.eddn_enabled.isChecked()

    dialog.eddn_enabled.setChecked(True)
    applied = Config()
    dialog.apply_to(applied)

    assert applied.eddn_enabled is True
    assert applied.eddn_uploader_id  # a UUID was minted
    dialog.deleteLater()
    qapp.processEvents()


def test_existing_eddn_uploader_id_is_preserved(qapp):
    dialog = SettingsDialog(
        Config(eddn_enabled=True, eddn_uploader_id="keep-me")
    )
    assert dialog.eddn_enabled.isChecked()

    applied = Config(eddn_uploader_id="keep-me")
    dialog.apply_to(applied)

    assert applied.eddn_uploader_id == "keep-me"
    dialog.deleteLater()
    qapp.processEvents()


def test_construction_sort_and_radius_round_trip(qapp):
    dialog = SettingsDialog(Config(stations_sort="match", stations_range_ly=0))
    # The far-right "Unlimited" end-stop maps back to 0.
    assert dialog.stations_range.value() > 500
    assert dialog._range_text(dialog.stations_range.value()) == "Unlimited"

    dialog.stations_sort.setCurrentIndex(dialog.stations_sort.findData("nearest"))
    dialog.stations_range.setValue(150)
    applied = Config()
    dialog.apply_to(applied)

    assert applied.stations_sort == "nearest"
    assert applied.stations_range_ly == 150
    dialog.deleteLater()
    qapp.processEvents()


def test_colonize_sort_and_weight_round_trip(qapp):
    dialog = SettingsDialog(Config(colonize_sort="balanced", colonize_body_weight=1.0))
    assert dialog.colonize_weight.isEnabled()  # balanced uses the weight

    dialog.colonize_weight.setValue(25)  # 2.5x
    dialog.colonize_sort.setCurrentIndex(dialog.colonize_sort.findData("nearest"))
    # Switching away from Balanced greys the weight out.
    assert not dialog.colonize_weight.isEnabled()

    applied = Config()
    dialog.apply_to(applied)
    assert applied.colonize_sort == "nearest"
    assert applied.colonize_body_weight == 2.5
    dialog.deleteLater()
    qapp.processEvents()


def test_gizmo_targeting_tracks_the_controls_tab_and_enable_toggle(qapp):
    from PySide6.QtTest import QSignalSpy

    dialog = SettingsDialog(Config(gizmo_enabled=True))
    spy = QSignalSpy(dialog.gizmo_targeting_changed)
    controls = dialog._controls_index

    # Opening on General emits nothing; arriving at Controls arms the crosshairs.
    dialog.tabs.setCurrentIndex(controls)
    assert spy.count() == 1 and spy.at(0)[0] is True

    # Leaving the tab disarms them...
    dialog.tabs.setCurrentIndex(0)
    assert spy.at(spy.count() - 1)[0] is False

    # ...and so does switching the gizmos off while the tab is open.
    dialog.tabs.setCurrentIndex(controls)
    dialog.gizmo_enabled.setChecked(False)
    assert spy.at(spy.count() - 1)[0] is False
    dialog.deleteLater()
    qapp.processEvents()


def test_about_tab_is_pinned_to_stock_orange_under_a_custom_hud(qapp):
    from PySide6.QtGui import QColor

    from edsc.gui import theme

    # Swap red<->blue so the live HUD accent is dragged well off stock orange.
    theme.apply_hud_matrix(((0.0, 0.0, 1.0), (0.0, 1.0, 0.0), (1.0, 0.0, 0.0)))
    try:
        dialog = SettingsDialog(Config())
        index = next(
            i
            for i in range(dialog.tabs.count())
            if dialog.tabs.tabText(i) == "About"
        )
        about = dialog.tabs.widget(index)
        stock_colour = QColor(*theme._BASE["ORANGE"])
        stock_orange = stock_colour.name()
        stock_selected = (
            f"rgba({stock_colour.red()},{stock_colour.green()},"
            f"{stock_colour.blue()},220)"
        )
        assert dialog.tabs.tabBar().styleSheet() == ""
        assert dialog._buttons.styleSheet() == ""
        # The About page carries its own stock-orange override...
        assert stock_orange in about.styleSheet()
        # ...even though the active palette is no longer orange.
        assert theme.ORANGE.name() != stock_orange

        dialog.tabs.setCurrentIndex(index)
        # Its selected tab and OK/Cancel actions sit outside the page subtree, but join the same stock homage for every interaction state.
        assert stock_selected in dialog.tabs.tabBar().styleSheet()
        assert stock_selected in dialog._buttons.styleSheet()
        assert theme.ORANGE.name() not in dialog.tabs.tabBar().styleSheet()
        assert theme.ORANGE.name() not in dialog._buttons.styleSheet()

        dialog.tabs.setCurrentIndex(0)
        # Leaving About restores the normal matrix-derived settings chrome.
        assert dialog.tabs.tabBar().styleSheet() == ""
        assert dialog._buttons.styleSheet() == ""
        dialog.deleteLater()
    finally:
        theme.apply_hud_matrix(None)
    qapp.processEvents()


def test_eddn_status_falls_back_to_config_state(qapp):
    off = SettingsDialog(Config(eddn_enabled=False))
    assert off._eddn_status_text() == "Sharing off."
    live = SettingsDialog(Config(eddn_enabled=True), eddn_status="sent 3")
    assert live._eddn_status_text() == "sent 3"
    for d in (off, live):
        d.deleteLater()
    qapp.processEvents()


def test_eddn_console_shows_recent_activity_newest_first(qapp):
    from edsc.eddn import ActivityLog

    log = ActivityLog()
    log.record("journal", "FSDJump · Sol").mark("sent")
    log.record("commodity", "Market · Sol / X · 3 goods").mark("rejected", "HTTP 400")
    uplink = SimpleNamespace(
        sender=SimpleNamespace(
            status_line=lambda: "sent 1 · rejected 1", activity=log
        )
    )
    dialog = SettingsDialog(
        Config(eddn_enabled=True, eddn_uploader_id="id"), eddn_uplink=uplink
    )

    lines = dialog._eddn_console.toPlainText().splitlines()
    assert "Market · Sol / X · 3 goods — HTTP 400" in lines[0]  # newest first
    assert "FSDJump · Sol" in lines[1]
    assert dialog._eddn_status_label.text() == "sent 1 · rejected 1"
    assert dialog._eddn_timer.isActive()  # live relay drives a refresh timer
    dialog.deleteLater()
    qapp.processEvents()


def test_eddn_console_is_static_without_a_live_relay(qapp):
    dialog = SettingsDialog(Config(eddn_enabled=False))
    assert dialog._eddn_console.toPlainText() == ""
    # No relay means nothing to poll, so no repaint timer is created.
    assert not hasattr(dialog, "_eddn_timer")
    dialog.deleteLater()
    qapp.processEvents()


def test_eddn_sync_button_disabled_without_a_callback(qapp):
    dialog = SettingsDialog(Config(eddn_enabled=False))
    assert not dialog._eddn_sync_button.isEnabled()
    dialog.deleteLater()
    qapp.processEvents()


def test_eddn_sync_button_pushes_and_reports_queued_rows(qapp):
    from edsc.eddn import ActivityLog

    uplink = SimpleNamespace(
        sender=SimpleNamespace(status_line=lambda: "sent 0", activity=ActivityLog())
    )
    calls = []
    dialog = SettingsDialog(
        Config(eddn_enabled=True, eddn_uploader_id="id"),
        eddn_uplink=uplink,
        on_eddn_sync=lambda: (calls.append(True), (True, True))[1],
    )
    assert dialog._eddn_sync_button.isEnabled()
    dialog._eddn_sync_button.click()
    assert calls == [True]
    text = dialog._eddn_sync_result.text()
    assert "market" in text and "location" in text
    dialog.deleteLater()
    qapp.processEvents()


def test_eddn_sync_button_reports_when_nothing_to_share(qapp):
    dialog = SettingsDialog(
        Config(eddn_enabled=True, eddn_uploader_id="id"),
        on_eddn_sync=lambda: (False, False),
    )
    dialog._eddn_sync_button.click()
    assert "Nothing to share" in dialog._eddn_sync_result.text()
    dialog.deleteLater()
    qapp.processEvents()


def test_sync_eddn_uplink_creates_and_arms_relay_when_watcher_ready():
    application = object.__new__(Application)
    application.config = Config(eddn_enabled=True, eddn_uploader_id="id")
    application.engine = SimpleNamespace(uplink=None, watcher=object())
    application.eddn_uplink = None

    application._sync_eddn_uplink()

    assert application.eddn_uplink is not None
    assert application.engine.uplink is application.eddn_uplink
    # Replay already finished (watcher present), so submissions are armed now.
    assert application.eddn_uplink._armed
    application.eddn_uplink.sender.close()


def test_sync_eddn_uplink_defers_arming_until_engine_is_ready():
    application = object.__new__(Application)
    application.config = Config(eddn_enabled=True, eddn_uploader_id="id")
    application.engine = SimpleNamespace(uplink=None, watcher=None)
    application.eddn_uplink = None

    application._sync_eddn_uplink()

    assert application.eddn_uplink is not None
    assert not application.eddn_uplink._armed  # engine.ready arms it later
    application.eddn_uplink.sender.close()


def test_sync_eddn_uplink_needs_a_minted_uploader_id():
    application = object.__new__(Application)
    application.config = Config(eddn_enabled=True, eddn_uploader_id="")
    application.engine = SimpleNamespace(uplink=None, watcher=object())
    application.eddn_uplink = None

    application._sync_eddn_uplink()

    assert application.eddn_uplink is None


def test_sync_eddn_uplink_tears_down_when_consent_is_withdrawn():
    application = object.__new__(Application)
    application.config = Config(eddn_enabled=False)
    sender = EddnSender("id")
    application.eddn_uplink = EddnUplink(sender)
    application.engine = SimpleNamespace(
        uplink=application.eddn_uplink, watcher=object()
    )

    application._sync_eddn_uplink()

    assert application.eddn_uplink is None
    assert application.engine.uplink is None
    assert sender._stop.is_set()  # the transport was shut down


def test_docked_opacity_settings_persist(tmp_path, monkeypatch):
    monkeypatch.setattr(config_module.paths, "config_dir", lambda: tmp_path)
    Config(auto_opacity_on_dock=True, docked_opacity=0.41).save()

    restored = Config.load()

    assert restored.auto_opacity_on_dock is True
    assert restored.docked_opacity == 0.41


def test_controller_selection_and_bindings_round_trip(qapp, tmp_path, monkeypatch):
    binding = {"kind": "button", "index": 6, "value": 1}
    original = Config(
        controller_device_id="linux:231d:0200:test",
        controller_bindings={"next_tab": binding},
    )
    dialog = SettingsDialog(original, development_mode=False)

    assert dialog.controller_tester.selected_device_id == original.controller_device_id
    assert dialog.controller_tester.binding_config == {"next_tab": binding}
    assert dialog.controller_tester.diagnostics.isHidden()

    applied = Config()
    dialog.apply_to(applied)
    monkeypatch.setattr(config_module.paths, "config_dir", lambda: tmp_path)
    applied.save()
    restored = Config.load()

    assert restored.controller_device_id == original.controller_device_id
    assert restored.controller_bindings == {"next_tab": binding}
    dialog.deleteLater()
    qapp.processEvents()


@pytest.mark.parametrize(
    ("docked", "enabled", "expected"),
    [
        (False, False, 0.84),
        (True, False, 0.84),
        (False, True, 0.84),
        (True, True, 0.36),
    ],
)
def test_docking_state_selects_the_configured_opacity(docked, enabled, expected):
    application = object.__new__(Application)
    application.config = Config(
        overlay_opacity=0.84,
        auto_opacity_on_dock=enabled,
        docked_opacity=0.36,
    )
    application.engine = SimpleNamespace(
        state=SimpleNamespace(docked_market_id=123 if docked else None)
    )
    application.overlay = Mock()

    application._sync_overlay_opacity()

    application.overlay.set_opacity.assert_called_once_with(expected)


def test_state_change_applies_docked_opacity_after_refresh():
    state = SimpleNamespace(docked_market_id=123)
    application = object.__new__(Application)
    application.config = Config(
        overlay_opacity=0.84,
        auto_opacity_on_dock=True,
        docked_opacity=0.36,
    )
    application.engine = SimpleNamespace(state=state)
    application.overlay = Mock()
    application.gizmos = Mock()

    application._on_state_changed()

    application.overlay.refresh.assert_called_once_with(state)
    application.overlay.set_opacity.assert_called_once_with(0.36)
    # Docking must reach the gizmos too: they hide while the controls are locked.
    application.gizmos.seed_docked.assert_called_once_with(True)


def test_open_settings_passes_the_live_controller_monitor(monkeypatch):
    captured = {}
    suspended_during_exec = []

    class FakeDialog:
        def __init__(self, config, parent, **kwargs):
            captured.update(config=config, parent=parent, **kwargs)

        def exec(self):
            suspended_during_exec.append(
                application._controller_bindings_suspended
            )
            return False

    application = object.__new__(Application)
    application.config = Config()
    application.overlay = object()
    application.controllers = object()
    monkeypatch.setattr(app_module, "SettingsDialog", FakeDialog)

    application.open_settings()

    assert captured["config"] is application.config
    assert captured["parent"] is application.overlay
    assert captured["controllers"] is application.controllers
    assert suspended_during_exec == [True]
    assert application._controller_bindings_suspended is False


@pytest.mark.parametrize(
    ("action_id", "method_name"),
    [
        ("previous_tab", "select_prev_tab"),
        ("next_tab", "select_next_tab"),
        ("toggle_collapsed", "toggle_collapsed"),
        ("refresh_search", "refresh_current_search"),
    ],
)
def test_controller_binding_dispatches_selected_device_action(
    action_id, method_name
):
    application = object.__new__(Application)
    application.config = Config(
        controller_device_id="selected",
        controller_bindings={
            action_id: {"kind": "button", "index": 3, "value": 1}
        },
    )
    application.overlay = Mock()
    application._controller_bindings_suspended = False

    application._on_controller_event(
        ControllerEvent("selected", "button", 3, 1)
    )

    getattr(application.overlay, method_name).assert_called_once_with()


def test_controller_dispatch_ignores_other_devices_and_non_activation_events():
    application = object.__new__(Application)
    application.config = Config(
        controller_device_id="selected",
        controller_bindings={
            "next_tab": {"kind": "button", "index": 3, "value": 1}
        },
    )
    application.overlay = Mock()
    application._controller_bindings_suspended = False

    for event in (
        ControllerEvent("other", "button", 3, 1),
        ControllerEvent("selected", "button", 3, 0),
        ControllerEvent("selected", "button", 3, 1, initial=True),
    ):
        application._on_controller_event(event)
    application._controller_bindings_suspended = True
    application._on_controller_event(
        ControllerEvent("selected", "button", 3, 1)
    )

    application.overlay.next_tab.assert_not_called()
    application.overlay.select_next_tab.assert_not_called()


def test_controller_opacity_toggles_between_full_and_contextual_value():
    application = object.__new__(Application)
    application.config = Config(
        overlay_opacity=0.84,
        auto_opacity_on_dock=True,
        docked_opacity=0.36,
    )
    application.engine = SimpleNamespace(
        state=SimpleNamespace(docked_market_id=123)
    )
    application.overlay = Mock()
    application._controller_full_opacity = False

    application._toggle_controller_opacity()
    application._toggle_controller_opacity()

    assert application.overlay.set_opacity.call_args_list == [
        call(1.0),
        call(0.36),
    ]


@pytest.mark.parametrize(
    ("docked", "enabled", "expected"),
    [
        (False, False, None),
        (True, False, None),
        (False, True, True),
        (True, True, False),
    ],
)
def test_docking_state_controls_collapse_when_enabled(docked, enabled, expected):
    application = object.__new__(Application)
    application.config = Config(auto_collapse_on_undock=enabled)
    application.engine = SimpleNamespace(
        state=SimpleNamespace(docked_market_id=123 if docked else None)
    )
    application.overlay = Mock()

    application._sync_overlay_collapsed()

    if expected is None:
        application.overlay.set_collapsed.assert_not_called()
    else:
        application.overlay.set_collapsed.assert_called_once_with(expected)


def test_live_docked_events_drive_overlay_state_without_market_id():
    application = object.__new__(Application)
    application.config = Config(
        overlay_opacity=0.84,
        auto_opacity_on_dock=True,
        docked_opacity=0.36,
        auto_collapse_on_undock=True,
    )
    application.engine = SimpleNamespace(state=SimpleNamespace(docked_market_id=None))
    application.overlay = Mock()
    application._flight_state = FlightStateTracker()

    application._on_live_event({"event": "Docked"})

    application.overlay.set_opacity.assert_called_once_with(0.36)
    application.overlay.set_collapsed.assert_called_once_with(False)

    application.overlay.reset_mock()
    application._on_live_event({"event": "Undocked"})

    application.overlay.set_opacity.assert_called_once_with(0.84)
    application.overlay.set_collapsed.assert_called_once_with(True)


def test_journal_ready_seeds_docked_state_from_replayed_location(monkeypatch):
    application = object.__new__(Application)
    journal_dir = object()
    application.engine = SimpleNamespace(
        state=SimpleNamespace(current_system="Sol", docked_market_id=None),
        journal_dir=journal_dir,
    )
    application.thargoid_effects = Mock()
    application.gizmos = Mock()
    application._flight_state = FlightStateTracker()
    monkeypatch.setattr(
        app_module.core,
        "read_session_events",
        lambda _journal_dir: [{"event": "Location", "Docked": True}],
    )

    application._on_journal_ready()

    application.gizmos.seed_docked.assert_called_once_with(True)
    application.gizmos.set_journal_dir.assert_called_once_with(journal_dir)