"""Tests for the console trace helpers and the filter census."""

# SPDX-License-Identifier: GPL-3.0-or-later

import pytest

from edsc import systems, trace


@pytest.fixture
def traced(monkeypatch):
    monkeypatch.setenv("EDSC_TRACE", "1")


def test_tracing_is_off_unless_a_switch_is_truthy(monkeypatch):
    monkeypatch.delenv("EDSC_TRACE", raising=False)
    monkeypatch.delenv("EDSC_DEV", raising=False)
    assert not trace.enabled()
    monkeypatch.setenv("EDSC_TRACE", "0")
    assert not trace.enabled()
    monkeypatch.setenv("EDSC_TRACE", "on")
    assert trace.enabled()
    # The broader development switch turns tracing on too.
    monkeypatch.setenv("EDSC_TRACE", "0")
    monkeypatch.setenv("EDSC_DEV", "1")
    assert trace.enabled()


def test_log_and_dump_are_silent_when_disabled(monkeypatch, capsys):
    monkeypatch.delenv("EDSC_TRACE", raising=False)
    monkeypatch.delenv("EDSC_DEV", raising=False)
    trace.log("nope")
    trace.dump("nope", {"a": 1})
    with trace.timed("nope"):
        pass
    assert capsys.readouterr().err == ""


def test_dump_truncates_long_payloads(traced, capsys):
    trace.dump("big", {"k": "x" * (trace._MAX_DUMP * 2)})
    err = capsys.readouterr().err
    assert "more chars)" in err
    assert len(err) < trace._MAX_DUMP * 2


def test_timed_reports_elapsed_and_the_note_even_when_raising(traced, capsys):
    with trace.timed("work") as note:
        note.say("42 rows")
    assert "work" in capsys.readouterr().err

    with pytest.raises(ValueError), trace.timed("boom"):
        raise ValueError("x")
    assert "boom" in capsys.readouterr().err  # a failure still reports its time


def _sys(name, **kw):
    return systems.SystemResult(name=name, steps=1, **kw)


def test_census_measures_each_active_axis_on_its_own():
    pool = [
        _sys("A", body_count=2, bodies=[]),
        _sys("B", body_count=40, bodies=[]),
    ]
    f = systems.SystemFilters(min_bodies=30, body_types=("ELW",))
    census = systems.filter_census(pool, f)
    # Measured in isolation: min_bodies rejects only A while body_types rejects both -- a sequential tally would have credited min_bodies for B too.
    assert census == {"min_bodies": 1, "body_types": 2}


def test_census_ignores_filters_left_at_their_neutral_default():
    pool = [_sys("A", body_count=5, bodies=[])]
    assert systems.filter_census(pool, systems.SystemFilters()) == {}
    # min_stars=1 is "Any", not a real restriction.
    assert systems.filter_census(pool, systems.SystemFilters(min_stars=1)) == {}


def test_rejection_reason_backs_passes_filters():
    s = _sys("A", body_count=2, bodies=[])
    f = systems.SystemFilters(min_bodies=30)
    assert systems.rejection_reason(s, f) == "min_bodies"
    assert not systems.passes_filters(s, f)
    assert systems.rejection_reason(s, systems.SystemFilters()) is None
    assert systems.passes_filters(s, systems.SystemFilters())
