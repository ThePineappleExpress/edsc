"""Tests for the Application's wiring glue, on partially built instances: ``object.__new__`` skips ``__init__`` (which would spawn the engine thread, global hotkeys, and the tray) and each test attaches only the collaborators its method actually touches -- the pattern test_thargoids and test_settings_dialog already use."""

# SPDX-License-Identifier: GPL-3.0-or-later

import json
from types import SimpleNamespace
from unittest.mock import Mock

from edsc.config import Config
from edsc.flight_state import FlightStateTracker
from edsc.gui import app as app_module
from edsc.gui.app import Application


def _app(**attrs):
    application = object.__new__(Application)
    for name, value in attrs.items():
        setattr(application, name, value)
    return application


def _overlay(collapsed=False, visible=False):
    return SimpleNamespace(
        collapsed=collapsed,
        isVisible=lambda: visible,
        set_collapsed=Mock(),
        show=Mock(),
        hide=Mock(),
        raise_=Mock(),
        set_opacity=Mock(),
        refresh=Mock(),
        select_prev_tab=Mock(),
        select_next_tab=Mock(),
        toggle_collapsed=Mock(),
        refresh_current_search=Mock(),
    )


def _tracker(docked):
    return SimpleNamespace(status=SimpleNamespace(docked=docked))


#  showing and hiding the overlay


def test_toggle_overlay_restores_a_collapsed_overlay():
    application = _app(overlay=_overlay(collapsed=True))
    application.toggle_overlay()
    application.overlay.set_collapsed.assert_called_once_with(False)
    application.overlay.hide.assert_not_called()


def test_toggle_overlay_hides_a_visible_overlay_and_shows_a_hidden_one():
    shown = _app(overlay=_overlay(visible=True))
    shown.toggle_overlay()
    shown.overlay.hide.assert_called_once_with()

    hidden = _app(overlay=_overlay(visible=False))
    hidden.toggle_overlay()
    hidden.overlay.show.assert_called_once_with()
    hidden.overlay.raise_.assert_called_once_with()


def test_activating_the_dock_anchor_reveals_whichever_form_is_hidden():
    collapsed = _app(overlay=_overlay(collapsed=True))
    collapsed._reveal_overlay()
    collapsed.overlay.set_collapsed.assert_called_once_with(False)

    expanded = _app(overlay=_overlay())
    expanded._reveal_overlay()
    expanded.overlay.show.assert_called_once_with()


#  docked-state resolution


def test_is_docked_prefers_the_live_flight_tracker():
    application = _app(
        _flight_state=_tracker(docked=True),
        engine=SimpleNamespace(state=SimpleNamespace(docked_market_id=None)),
    )
    assert application._is_docked() is True


def test_is_docked_falls_back_to_app_state_when_the_tracker_is_silent():
    application = _app(
        _flight_state=_tracker(docked=None),
        engine=SimpleNamespace(state=SimpleNamespace(docked_market_id=42)),
    )
    assert application._is_docked() is True
    application.engine.state.docked_market_id = None
    assert application._is_docked() is False


def test_replayed_docked_state_reads_the_session_journal(tmp_path):
    (tmp_path / "Journal.2026-07-06T000000.01.log").write_text(
        json.dumps({"event": "Docked", "MarketID": 1}) + "\n"
        + json.dumps({"event": "Undocked", "MarketID": 1}) + "\n",
        encoding="utf-8",
    )
    application = _app(engine=SimpleNamespace(
        journal_dir=tmp_path,
        state=SimpleNamespace(docked_market_id=99),  # stale: journal wins
    ))
    assert application._replayed_docked_state() is False


def test_replayed_docked_state_falls_back_to_app_state_without_a_journal():
    application = _app(engine=SimpleNamespace(
        journal_dir=None,
        state=SimpleNamespace(docked_market_id=42),
    ))
    assert application._replayed_docked_state() is True


#  overlay opacity


def _opacity_app(config, docked, full=False):
    return _app(
        config=config,
        overlay=_overlay(),
        _flight_state=_tracker(docked=docked),
        engine=SimpleNamespace(state=SimpleNamespace(docked_market_id=None)),
        _controller_full_opacity=full,
    )


def test_opacity_follows_docked_state_only_when_automatic():
    config = Config(overlay_opacity=0.9, docked_opacity=0.5,
                    auto_opacity_on_dock=False)
    application = _opacity_app(config, docked=True)
    application._sync_overlay_opacity()
    application.overlay.set_opacity.assert_called_once_with(0.9)

    config.auto_opacity_on_dock = True
    application = _opacity_app(config, docked=True)
    application._sync_overlay_opacity()
    application.overlay.set_opacity.assert_called_once_with(0.5)

    application = _opacity_app(config, docked=False)
    application._sync_overlay_opacity()
    application.overlay.set_opacity.assert_called_once_with(0.9)


def test_controller_opacity_override_wins_and_toggles_back():
    config = Config(overlay_opacity=0.9, docked_opacity=0.5,
                    auto_opacity_on_dock=True)
    application = _opacity_app(config, docked=True)

    application._toggle_controller_opacity()
    application.overlay.set_opacity.assert_called_once_with(1.0)

    application._toggle_controller_opacity()
    assert application.overlay.set_opacity.call_args.args == (0.5,)


#  automatic collapse


def test_auto_collapse_follows_docking_only_when_enabled():
    config = Config(auto_collapse_on_undock=False)
    application = _app(
        config=config,
        overlay=_overlay(),
        _flight_state=_tracker(docked=False),
        engine=SimpleNamespace(state=SimpleNamespace(docked_market_id=None)),
    )
    application._sync_overlay_collapsed()
    application.overlay.set_collapsed.assert_not_called()

    config.auto_collapse_on_undock = True
    application._sync_overlay_collapsed()
    application.overlay.set_collapsed.assert_called_once_with(True)

    application._flight_state = _tracker(docked=True)
    application._sync_overlay_collapsed()
    assert application.overlay.set_collapsed.call_args.args == (False,)


#  live events


def test_live_event_resyncs_the_hud_only_on_a_flight_state_change():
    application = _app(
        config=Config(auto_collapse_on_undock=True),
        overlay=_overlay(),
        _flight_state=FlightStateTracker(),
        engine=SimpleNamespace(state=SimpleNamespace(docked_market_id=None)),
        _controller_full_opacity=False,
    )
    application._on_live_event({"event": "Docked", "MarketID": 1})
    application.overlay.set_opacity.assert_called_once()
    application.overlay.set_collapsed.assert_called_once_with(False)

    application.overlay.set_opacity.reset_mock()
    application._on_live_event({"event": "Music"})  # no flight-state change
    application.overlay.set_opacity.assert_not_called()


#  controller bindings


def _controller_app(monkeypatch, action_id):
    monkeypatch.setattr(
        app_module, "action_for_event", lambda bindings, event: action_id
    )
    return _app(
        config=Config(controller_device_id="dev-1"),
        overlay=_overlay(),
        _controller_bindings_suspended=False,
    )


def test_controller_event_dispatches_the_bound_action(monkeypatch):
    application = _controller_app(monkeypatch, "next_tab")
    application._on_controller_event(SimpleNamespace(device_id="dev-1"))
    application.overlay.select_next_tab.assert_called_once_with()


def test_controller_events_ignored_for_other_devices_and_while_suspended(
    monkeypatch,
):
    application = _controller_app(monkeypatch, "next_tab")
    application._on_controller_event(SimpleNamespace(device_id="other"))
    application.overlay.select_next_tab.assert_not_called()

    application._controller_bindings_suspended = True
    application._on_controller_event(SimpleNamespace(device_id="dev-1"))
    application.overlay.select_next_tab.assert_not_called()


def test_an_unbound_controller_event_is_inert(monkeypatch):
    application = _controller_app(monkeypatch, "not-an-action")
    application._on_controller_event(SimpleNamespace(device_id="dev-1"))
    application.overlay.select_next_tab.assert_not_called()


#  EDDN relay lifecycle


class _StubSender:
    def __init__(self, uploader_id):
        self.uploader_id = uploader_id
        self.closed = False

    def close(self):
        self.closed = True


class _StubUplink:
    def __init__(self, sender):
        self.sender = sender
        self.armed = 0

    def arm(self):
        self.armed += 1


def _eddn_app(monkeypatch, *, enabled, watcher=None):
    monkeypatch.setattr(app_module, "EddnSender", _StubSender)
    monkeypatch.setattr(app_module, "EddnUplink", _StubUplink)
    return _app(
        config=Config(eddn_enabled=enabled, eddn_uploader_id="uid-1"),
        engine=SimpleNamespace(uplink=None, watcher=watcher),
        eddn_uplink=None,
    )


def test_consent_creates_the_relay_and_wires_it_to_the_engine(monkeypatch):
    application = _eddn_app(monkeypatch, enabled=True)
    application._sync_eddn_uplink()
    uplink = application.eddn_uplink
    assert uplink is not None
    assert uplink.sender.uploader_id == "uid-1"
    assert application.engine.uplink is uplink
    assert uplink.armed == 0  # replay not finished: armed later via ready

    # Already consented and built: a second sync must not rebuild it.
    application._sync_eddn_uplink()
    assert application.eddn_uplink is uplink


def test_consent_after_replay_arms_immediately(monkeypatch):
    application = _eddn_app(monkeypatch, enabled=True, watcher=object())
    application._sync_eddn_uplink()
    assert application.eddn_uplink.armed == 1


def test_withdrawing_consent_tears_the_relay_down(monkeypatch):
    application = _eddn_app(monkeypatch, enabled=True)
    application._sync_eddn_uplink()
    sender = application.eddn_uplink.sender

    application.config.eddn_enabled = False
    application._sync_eddn_uplink()

    assert application.eddn_uplink is None
    assert application.engine.uplink is None
    assert sender.closed


#  carrier reset


def test_reset_carrier_clears_persists_and_rerenders(monkeypatch):
    saved = []
    monkeypatch.setattr(app_module.core, "save_state", saved.append)
    state = SimpleNamespace(carrier_cargo={"steel": 500})
    application = _app(
        engine=SimpleNamespace(state=state),
        overlay=_overlay(),
    )

    application.reset_carrier()

    assert state.carrier_cargo == {}
    assert saved == [state]
    application.overlay.refresh.assert_called_once_with(state)


#  shutdown


def test_shutdown_stops_every_component_and_saves():
    uplink = SimpleNamespace(sender=SimpleNamespace(close=Mock()))
    application = _app(
        overlay=SimpleNamespace(persist_geometry=Mock(), stop=Mock()),
        gizmos=SimpleNamespace(save_positions=Mock(), stop=Mock()),
        hotkeys=SimpleNamespace(stop=Mock()),
        controllers=SimpleNamespace(stop=Mock()),
        thargoid_effects=SimpleNamespace(reset=Mock()),
        eddn_uplink=uplink,
        anchor=SimpleNamespace(close=Mock()),
        config=SimpleNamespace(save=Mock()),
        engine=SimpleNamespace(stop=Mock()),
    )

    application._shutdown()

    application.overlay.persist_geometry.assert_called_once_with()
    application.overlay.stop.assert_called_once_with()
    application.gizmos.save_positions.assert_called_once_with()
    application.gizmos.stop.assert_called_once_with()
    application.hotkeys.stop.assert_called_once_with()
    application.controllers.stop.assert_called_once_with()
    application.thargoid_effects.reset.assert_called_once_with()
    uplink.sender.close.assert_called_once_with()
    application.anchor.close.assert_called_once_with()
    application.config.save.assert_called_once_with()
    application.engine.stop.assert_called_once_with()
