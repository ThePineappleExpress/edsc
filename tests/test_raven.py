import io
import json
import urllib.error
from contextlib import contextmanager
from unittest import mock

import pytest

from edsc import raven

_EMPTY = object()  # route value for a 204/empty response body


class _FakeApi:
    """urlopen stand-in: serves a canned body/exception per requested path; ``routes`` maps a path (after :data:`raven.API_BASE`) to a JSON-able payload, an ``Exception`` to raise, or :data:`_EMPTY` for an empty body. Records the paths requested."""

    def __init__(self, routes):
        self.routes = routes
        self.requested = []
        self.user_agents = []

    @contextmanager
    def __call__(self, req, timeout=0):
        path = req.full_url[len(raven.API_BASE):]
        self.requested.append(path)
        self.user_agents.append(req.get_header("User-agent"))
        payload = self.routes[path]
        if isinstance(payload, Exception):
            raise payload
        if payload is _EMPTY:
            yield io.BytesIO(b"")
            return
        yield io.BytesIO(json.dumps(payload).encode())


def _patch(fake):
    return mock.patch.object(raven.urllib.request, "urlopen", fake)


_SOL = {
    "rev": 12,
    "cmdr": "Frost912",
    "name": "Sol",
    "id64": 10477373803,
    "architect": "Cloudas",
    "pos": [0, 0, 0],
    "reserveLevel": "common",
    "bodies": [
        {"name": "Sol", "num": 0, "distLS": 0, "type": "st",
         "subType": "G (White-Yellow) Star", "features": []},
        {"name": "Earth", "num": 3, "distLS": 492.2, "type": "elw",
         "subType": "Earth-like world", "features": ["volcanism", "landable"]},
    ],
}


#  transport / headers


def test_get_sends_user_agent_and_decodes_json():
    fake = _FakeApi({"/api/v2/system/Sol": _SOL})
    with _patch(fake):
        data = raven.get("/api/v2/system/Sol")
    assert data["name"] == "Sol"
    assert fake.user_agents == [raven.USER_AGENT]
    assert raven.USER_AGENT.startswith("EDSC/")


def test_system_ref_encodes_names_and_stringifies_id64():
    assert raven._system_ref("Col 285 Sector") == "Col%20285%20Sector"
    assert raven._system_ref(10477373803) == "10477373803"
    assert raven._system_ref("  Sol  ") == "Sol"


#  fetch_system


def test_fetch_system_parses_identity_and_bodies():
    fake = _FakeApi({"/api/v2/system/Sol": _SOL})
    with _patch(fake):
        sys = raven.fetch_system("Sol")
    assert sys is not None
    assert sys.id64 == 10477373803
    assert sys.architect == "Cloudas"
    assert sys.reserve_level == "common"
    assert sys.pos == (0.0, 0.0, 0.0)
    assert sys.revision == 12
    assert sys.body_count == 2
    earth = sys.bodies[1]
    assert earth.type == "elw"
    assert earth.is_landable is True
    assert sys.bodies[0].is_landable is False


def test_fetch_system_accepts_id64():
    fake = _FakeApi({"/api/v2/system/10477373803": _SOL})
    with _patch(fake):
        assert raven.fetch_system(10477373803) is not None
    assert fake.requested == ["/api/v2/system/10477373803"]


@pytest.mark.parametrize("code", [400, 404])
def test_fetch_system_returns_none_when_untracked(code):
    # Unknown name -> 400, unknown id64 -> 404; both mean "no data".
    err = urllib.error.HTTPError("u", code, "nope", {}, None)
    fake = _FakeApi({"/api/v2/system/Nowhere": err})
    with _patch(fake):
        assert raven.fetch_system("Nowhere") is None


def test_fetch_system_wraps_server_error():
    err = urllib.error.HTTPError("u", 500, "boom", {}, None)
    fake = _FakeApi({"/api/v2/system/Sol": err})
    with _patch(fake), pytest.raises(raven.RavenError):
        raven.fetch_system("Sol")


def test_fetch_system_tolerates_missing_keys():
    fake = _FakeApi({"/api/v2/system/Bare": {"name": "Bare"}})
    with _patch(fake):
        sys = raven.fetch_system("Bare")
    assert sys is not None
    assert sys.id64 is None
    assert sys.architect == ""
    assert sys.pos is None
    assert sys.bodies == []


#  fetch_sites


def test_fetch_sites_parses_and_flags_completion():
    sites = [
        {"id": "&1", "name": "Daedalus", "bodyNum": 1, "buildType": "apollo",
         "status": "complete", "buildId": "abc-123"},
        {"id": "&2", "name": "Slot", "bodyNum": 20, "buildType": None,
         "status": "plan"},
    ]
    fake = _FakeApi({"/api/v2/system/Sol/sites": sites})
    with _patch(fake):
        parsed = raven.fetch_sites("Sol")
    assert len(parsed) == 2
    assert parsed[0].build_id == "abc-123"
    assert parsed[0].is_complete is True
    assert parsed[1].build_type == ""  # null coerced to empty
    assert parsed[1].build_id is None
    assert parsed[1].is_complete is False


@pytest.mark.parametrize("code", [400, 404])
def test_fetch_sites_empty_when_untracked(code):
    err = urllib.error.HTTPError("u", code, "nope", {}, None)
    fake = _FakeApi({"/api/v2/system/Nowhere/sites": err})
    with _patch(fake):
        assert raven.fetch_sites("Nowhere") == []


def test_fetch_sites_raises_on_server_error():
    # Unknown id64 to /sites returns 500 -- a real error, not "untracked".
    err = urllib.error.HTTPError("u", 500, "boom", {}, None)
    fake = _FakeApi({"/api/v2/system/99999/sites": err})
    with _patch(fake), pytest.raises(raven.RavenError):
        raven.fetch_sites(99999)


def test_fetch_sites_rejects_non_list():
    fake = _FakeApi({"/api/v2/system/Sol/sites": {"unexpected": True}})
    with _patch(fake), pytest.raises(raven.RavenError):
        raven.fetch_sites("Sol")


#  fetch_spansh_economies


def test_fetch_spansh_economies_parses_shares():
    econ = [
        {"id": 128016384, "updated": "2026-07-12 09:34:49+00",
         "economies": {"refinery": 100}},
        {"id": 3534390528, "updated": "2026-06-21 14:19:20+00",
         "economies": {"colony": 100}},
    ]
    fake = _FakeApi({"/api/v2/system/Sol/spanshEconomies": econ})
    with _patch(fake):
        parsed = raven.fetch_spansh_economies("Sol")
    assert parsed[0].market_id == 128016384
    assert parsed[0].economies == {"refinery": 100.0}
    assert parsed[1].economies == {"colony": 100.0}


def test_fetch_spansh_economies_empty_on_204():
    # A known-but-bare system answers /spanshEconomies with a 204 empty body.
    fake = _FakeApi({"/api/v2/system/Sol/spanshEconomies": _EMPTY})
    with _patch(fake):
        assert raven.fetch_spansh_economies("Sol") == []
