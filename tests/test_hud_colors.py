from pathlib import Path

from edsc import hud_colors

SWAP_RED_GREEN = """<?xml version="1.0" encoding="UTF-8" ?>
<GraphicsConfig>
  <GUIColour>
    <Default>
      <LocalisationName>Standard</LocalisationName>
      <MatrixRed> 0, 1, 0 </MatrixRed>
      <MatrixGreen> 1, 0, 0 </MatrixGreen>
      <MatrixBlue> 0, 0, 1 </MatrixBlue>
    </Default>
  </GUIColour>
</GraphicsConfig>
"""

RED_ROW_ONLY = """<?xml version="1.0" encoding="UTF-8" ?>
<GraphicsConfig>
  <GUIColour>
    <Default>
      <MatrixRed>0.5, 0.5, 0</MatrixRed>
    </Default>
  </GUIColour>
</GraphicsConfig>
"""


def _no_steam(monkeypatch):
    """Keep tests hermetic: never probe this machine's real Steam installs."""
    monkeypatch.setattr(hud_colors, "steam_library_folders", lambda: [])
    monkeypatch.delenv("EDSC_GRAPHICS_CONFIG", raising=False)


def test_transform_identity_is_noop():
    assert hud_colors.transform(hud_colors.IDENTITY, (255, 130, 20)) == (255, 130, 20)


def test_transform_mixes_channels_and_clamps():
    matrix = ((2.0, 0.0, 0.0), (0.0, 1.0, 0.0), (-1.0, 0.0, 1.0))
    assert hud_colors.transform(matrix, (200, 100, 50)) == (255, 100, 0)


def test_env_override_wins(tmp_path, monkeypatch):
    _no_steam(monkeypatch)
    config = tmp_path / "GraphicsConfiguration.xml"
    config.write_text(SWAP_RED_GREEN)
    monkeypatch.setenv("EDSC_GRAPHICS_CONFIG", str(config))
    assert hud_colors.load_matrix() == ((0, 1, 0), (1, 0, 0), (0, 0, 1))


def test_env_partial_rows_fill_identity(tmp_path, monkeypatch):
    _no_steam(monkeypatch)
    config = tmp_path / "GraphicsConfiguration.xml"
    config.write_text(RED_ROW_ONLY)
    monkeypatch.setenv("EDSC_GRAPHICS_CONFIG", str(config))
    assert hud_colors.load_matrix() == ((0.5, 0.5, 0), (0, 1, 0), (0, 0, 1))


def test_env_pointing_at_garbage_gives_none(tmp_path, monkeypatch):
    _no_steam(monkeypatch)
    config = tmp_path / "GraphicsConfiguration.xml"
    config.write_text("<GraphicsConfig><GUIColour>")  # malformed
    monkeypatch.setenv("EDSC_GRAPHICS_CONFIG", str(config))
    assert hud_colors.load_matrix() is None


def test_nothing_found_gives_none(monkeypatch):
    _no_steam(monkeypatch)
    assert hud_colors.load_matrix() is None


def _fake_profile(tmp_path: Path, override_xml: str) -> Path:
    """Build a user profile with journals + graphics override; returns journal dir."""
    journal_dir = tmp_path / "Saved Games" / "Frontier Developments" / "Elite Dangerous"
    journal_dir.mkdir(parents=True)
    override = (
        tmp_path
        / "AppData"
        / "Local"
        / "Frontier Developments"
        / "Elite Dangerous"
        / "Options"
        / "Graphics"
        / "GraphicsConfigurationOverride.xml"
    )
    override.parent.mkdir(parents=True)
    override.write_text(override_xml)
    return journal_dir


def test_override_found_via_journal_dir(tmp_path, monkeypatch):
    _no_steam(monkeypatch)
    journal_dir = _fake_profile(tmp_path, SWAP_RED_GREEN)
    assert hud_colors.load_matrix(journal_dir) == ((0, 1, 0), (1, 0, 0), (0, 0, 1))


def test_override_rows_beat_game_config(tmp_path, monkeypatch):
    monkeypatch.delenv("EDSC_GRAPHICS_CONFIG", raising=False)
    journal_dir = _fake_profile(tmp_path, RED_ROW_ONLY)

    library = tmp_path / "steam_library"
    game_config = (
        library
        / "steamapps"
        / "common"
        / "Elite Dangerous"
        / "Products"
        / "elite-dangerous-odyssey-64"
        / "GraphicsConfiguration.xml"
    )
    game_config.parent.mkdir(parents=True)
    game_config.write_text(SWAP_RED_GREEN)
    monkeypatch.setattr(hud_colors, "steam_library_folders", lambda: [library])

    # Red row comes from the user override, green/blue from the game file.
    assert hud_colors.load_matrix(journal_dir) == (
        (0.5, 0.5, 0),
        (1, 0, 0),
        (0, 0, 1),
    )
