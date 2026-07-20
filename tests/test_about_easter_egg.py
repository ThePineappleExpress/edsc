
import pytest
from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QLabel, QMessageBox, QVBoxLayout, QWidget

from edsc.config import Config
from edsc.gui import about_easter_egg as egg_module, theme
from edsc.gui.settings_dialog import SettingsDialog
from edsc.paths import asset_path


def _shown_about_dialog(qapp, parent=None):
    dialog = SettingsDialog(Config(), parent)
    dialog.tabs.setCurrentIndex(dialog._about_index)
    dialog.show()
    qapp.processEvents()
    return dialog


def _dispose(dialog, qapp):
    dialog._about_easter_egg.reset()
    dialog.close()
    dialog.deleteLater()
    qapp.processEvents()


def test_eighth_about_logo_click_runs_the_complete_timed_sequence(
    qapp, monkeypatch
):
    now = [100.0]
    monkeypatch.setattr(egg_module.time, "monotonic", lambda: now[0])
    dialog = _shown_about_dialog(qapp)
    controller = dialog._about_easter_egg

    for _ in range(controller.REQUIRED_CLICKS - 1):
        QTest.mouseClick(dialog._about_logo, Qt.LeftButton)
    assert not controller.active

    QTest.mouseClick(dialog._about_logo, Qt.LeftButton)

    assert controller.active
    assert controller.phase == "flicker"
    assert controller._takeover.isVisible()
    assert not controller._takeover.snapshot.isNull()
    assert any(fragment.text == "About" for fragment in controller._takeover.fragments)
    assert any(
        "Elite Dangerous Supply Chain" in fragment.text
        for fragment in controller._takeover.fragments
    )
    assert not controller._takeover.thargoid.isNull()
    assert not controller._takeover.antixeno.isNull()
    assert controller.HYDRA_SECONDS == 5.6
    assert controller.RESTORE_SECONDS == 0.45

    sequence = (
        (controller.FLICKER_SECONDS, "void"),
        (controller.VOID_SECONDS, "hydra"),
        (controller.HYDRA_SECONDS, "after_hydra"),
        (controller.AFTER_HYDRA_SECONDS, "message_one"),
        (controller.MESSAGE_SECONDS, "message_two"),
        (controller.MESSAGE_SECONDS, "axi_fade"),
        (controller.AXI_FADE_SECONDS, "axi_wait"),
    )
    for duration, expected in sequence:
        now[0] += duration + 0.001
        controller._tick()
        assert controller.phase == expected

    assert not controller._timer.isActive()
    assert controller._takeover._axi_interactive
    assert controller._takeover.axi_copy.isVisible()
    assert controller._takeover.axi_copy.toPlainText() == (
        asset_path("war.md").read_text(encoding="utf-8").strip()
    )
    assert controller._takeover._axi_rect().width() == theme.METRICS.about_icon_px
    _dispose(dialog, qapp)


@pytest.mark.parametrize(
    ("answer", "expected_url_count"),
    [
        (QMessageBox.Yes, 1),
        (QMessageBox.No, 0),
    ],
)
def test_humanity_prompt_opens_axi_only_for_yes_then_flickers_back_to_normal(
    qapp, monkeypatch, answer, expected_url_count
):
    now = [100.0]
    monkeypatch.setattr(egg_module.time, "monotonic", lambda: now[0])
    dialog = _shown_about_dialog(qapp)
    controller = dialog._about_easter_egg
    controller._active = True
    controller._takeover.setGeometry(dialog.panel.rect())
    controller._takeover.snapshot = dialog.panel.grab()
    controller._takeover.show()
    controller._set_phase("axi_wait")
    questions = []
    opened = []

    def ask(*args):
        questions.append(args)
        return answer

    monkeypatch.setattr(egg_module.QMessageBox, "question", ask)
    monkeypatch.setattr(
        egg_module.QDesktopServices,
        "openUrl",
        lambda url: opened.append(url) or True,
    )

    controller._ask_humanity()

    assert questions[0][2] == "Do you want to save humanity?"
    assert len(opened) == expected_url_count
    if opened:
        assert opened[0].toString() == egg_module.ANTI_XENO_URL
    assert controller.active
    assert controller.phase == "restore"
    assert controller._takeover.isVisible()
    assert not controller._takeover._axi_interactive
    assert not controller._takeover.axi_copy.isVisible()

    now[0] += controller.RESTORE_SECONDS + 0.001
    controller._tick()

    assert not controller.active
    assert controller.phase == "idle"
    assert not controller._takeover.isVisible()
    assert controller._clicks == 0
    _dispose(dialog, qapp)


def test_main_app_window_keeps_glitching_until_fast_flicker_in(
    qapp, monkeypatch
):
    now = [100.0]
    monkeypatch.setattr(egg_module.time, "monotonic", lambda: now[0])
    main = QWidget()
    main.resize(640, 420)
    layout = QVBoxLayout(main)
    layout.addWidget(QLabel("Main app content"))
    main.show()
    dialog = _shown_about_dialog(qapp, main)
    controller = dialog._about_easter_egg

    controller._start()

    assert controller._main_takeover is not None
    assert controller._main_takeover.isVisible()
    assert controller._main_takeover.phase == "flicker"
    assert not controller._main_takeover.snapshot.isNull()
    assert any(
        fragment.text == "Main app content"
        for fragment in controller._main_takeover.fragments
    )

    now[0] += controller.FLICKER_SECONDS + 0.001
    controller._tick()
    assert controller.phase == "void"
    assert controller._main_takeover.isVisible()
    assert controller._main_takeover.phase == "glitch"
    assert controller._main_glitch_timer.isActive()

    progress = controller._main_takeover.progress
    controller._advance_main_glitch()
    assert controller._main_takeover.progress > progress

    for phase in (
        "hydra",
        "after_hydra",
        "message_one",
        "message_two",
        "axi_fade",
        "axi_wait",
    ):
        controller._set_phase(phase)
        assert controller._main_takeover.isVisible()
        assert controller._main_takeover.phase == "glitch"
        assert controller._main_glitch_timer.isActive()

    controller._set_phase("restore")
    assert controller._main_takeover.isVisible()
    assert controller._main_takeover.phase == "restore"
    assert not controller._main_glitch_timer.isActive()

    now[0] += controller.RESTORE_SECONDS + 0.001
    controller._tick()
    assert not controller.active
    assert not controller._main_takeover.isVisible()

    _dispose(dialog, qapp)
    main.close()
    main.deleteLater()
    qapp.processEvents()
