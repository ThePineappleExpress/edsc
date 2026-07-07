from edsc.journal import locator


def test_env_override_wins(tmp_path, monkeypatch):
    (tmp_path / "Journal.2026-01-01T000000.01.log").write_text("{}\n")
    monkeypatch.setenv("EDSC_JOURNAL_DIR", str(tmp_path))
    assert locator.find_journal_dir() == tmp_path


def test_explicit_override_beats_env(tmp_path, monkeypatch):
    monkeypatch.setenv("EDSC_JOURNAL_DIR", "/does/not/exist")
    assert locator.find_journal_dir(str(tmp_path)) == tmp_path


def test_missing_override_falls_through(monkeypatch):
    monkeypatch.setenv("EDSC_JOURNAL_DIR", "/definitely/not/here/edsc")
    # Falls through to platform auto-detection (may be None on CI).
    result = locator.find_journal_dir()
    assert result is None or result.is_dir()


def test_latest_and_all_journals(tmp_path):
    older = tmp_path / "Journal.2026-01-01T000000.01.log"
    newer = tmp_path / "Journal.2026-02-01T000000.01.log"
    older.write_text("{}\n")
    newer.write_text("{}\n")
    assert locator.latest_journal(tmp_path) == newer
    assert locator.all_journals(tmp_path) == [older, newer]


def test_latest_journal_none_when_empty(tmp_path):
    assert locator.latest_journal(tmp_path) is None
