import io
import json
from contextlib import contextmanager
from unittest import mock

from edsc import stations


def _station(name, system, distance, arrival, large, market, planetary=False):
    """Build a raw Spansh-style station dict with a full market array."""
    return {
        "name": name,
        "system_name": system,
        "distance": distance,
        "distance_to_arrival": arrival,
        "has_large_pad": large,
        "is_planetary": planetary,
        "type": "Coriolis Starport",
        "market_id": abs(hash(name)) % 1_000_000,
        "market_updated_at": "2026-07-01T00:00:00Z",
        "market": [
            {"commodity": c, "supply": s} for c, s in market.items()
        ],
    }


# Two candidate stations. Alpha stocks both needs; Beta only stocks Aluminium.
ALPHA = _station("Alpha", "Sol", 5.0, 100, True,
                 {"Aluminium": 5000, "Steel": 8000, "Copper": 0})
BETA = _station("Beta", "Wolf 359", 3.0, 50, True,
                {"Aluminium": 2000, "Water": 100})


@contextmanager
def _fake_urlopen(req, timeout=0):
    body = json.loads(req.data.decode())
    commodity = body["filters"]["market"][0]["name"]
    # Alpha appears for any needed commodity it stocks; Beta only for Aluminium.
    results = []
    if commodity in ("Aluminium", "Steel"):
        results.append(ALPHA)
    if commodity == "Aluminium":
        results.append(BETA)
    payload = {"count": len(results), "reference": {"name": "Sol"},
               "results": results}
    yield io.BytesIO(json.dumps(payload).encode())


def test_search_ranks_by_coverage_then_distance():
    with mock.patch.object(stations.urllib.request, "urlopen", _fake_urlopen):
        results = stations.search_stations("Sol", ["Aluminium", "Steel"])
    assert [r.name for r in results] == ["Alpha", "Beta"]
    alpha, beta = results
    # Alpha covers both requested commodities from its full market.
    assert alpha.match_count == 2
    assert alpha.needed_total == 2
    assert alpha.satisfaction == 1.0
    # Beta only stocks Aluminium (Water isn't requested; supply 0 doesn't count).
    assert beta.match_count == 1
    assert beta.satisfaction == 0.5


def test_supply_zero_is_not_counted():
    with mock.patch.object(stations.urllib.request, "urlopen", _fake_urlopen):
        results = stations.search_stations("Sol", ["Copper"])
    # Alpha lists Copper but with supply 0 -> no station actually stocks it.
    assert results == []


def test_blank_reference_system_raises():
    try:
        stations.search_stations("", ["Aluminium"])
    except stations.StationSearchError:
        pass
    else:  # pragma: no cover
        raise AssertionError("expected StationSearchError")


def test_empty_needed_returns_empty():
    assert stations.search_stations("Sol", []) == []


def test_name_normalisation_matches_spacing_and_case():
    st = _station("Gamma", "Sol", 1.0, 10, True, {"CMM Composite": 300})
    @contextmanager
    def fake(req, timeout=0):
        payload = {"results": [st]}
        yield io.BytesIO(json.dumps(payload).encode())

    with mock.patch.object(stations.urllib.request, "urlopen", fake):
        results = stations.search_stations("Sol", ["cmm  composite"])
    assert results and results[0].match_count == 1


def test_supply_must_cover_shortfall_when_amounts_given():
    """With needed amounts, token supply no longer counts as coverage."""
    st = _station("Delta", "Sol", 1.0, 10, True,
                  {"Steel": 40, "Aluminium": 500})

    @contextmanager
    def fake(req, timeout=0):
        payload = {"results": [st]}
        yield io.BytesIO(json.dumps(payload).encode())

    with mock.patch.object(stations.urllib.request, "urlopen", fake):
        results = stations.search_stations(
            "Sol", {"Steel": 5000, "Aluminium": 200}
        )
    # Steel needs 5000 -> threshold is the 100 t floor; 40 t doesn't count.
    # Aluminium needs 200 -> threshold 100; 500 t counts.
    assert results and results[0].matched == ["Aluminium"]


def test_small_shortfall_matches_small_supply():
    st = _station("Echo", "Sol", 1.0, 10, True, {"Steel": 40})

    @contextmanager
    def fake(req, timeout=0):
        payload = {"results": [st]}
        yield io.BytesIO(json.dumps(payload).encode())

    with mock.patch.object(stations.urllib.request, "urlopen", fake):
        results = stations.search_stations("Sol", {"Steel": 25})
    # Only 25 t still needed: a 40 t supply covers it fully.
    assert results and results[0].matched == ["Steel"]


def test_planetary_included_by_default():
    surface = _station("Ground", "Sol", 1.0, 10, True, {"Steel": 5000},
                       planetary=True)
    bodies = []

    @contextmanager
    def fake(req, timeout=0):
        bodies.append(json.loads(req.data.decode()))
        payload = {"results": [surface]}
        yield io.BytesIO(json.dumps(payload).encode())

    with mock.patch.object(stations.urllib.request, "urlopen", fake):
        results = stations.search_stations("Sol", ["Steel"])
    assert [r.name for r in results] == ["Ground"]
    assert results[0].is_planetary
    # Default search doesn't restrict on surface stations.
    assert "is_planetary" not in bodies[0]["filters"]


def test_planetary_excluded_when_toggled_off():
    surface = _station("Ground", "Sol", 1.0, 10, True, {"Steel": 5000},
                       planetary=True)
    orbital = _station("Orbit", "Sol", 2.0, 20, True, {"Steel": 5000})
    bodies = []

    @contextmanager
    def fake(req, timeout=0):
        bodies.append(json.loads(req.data.decode()))
        payload = {"results": [surface, orbital]}
        yield io.BytesIO(json.dumps(payload).encode())

    with mock.patch.object(stations.urllib.request, "urlopen", fake):
        results = stations.search_stations(
            "Sol", ["Steel"], include_planetary=False
        )
    # The query asks Spansh to skip surface stations, and any that slip
    # through anyway are dropped in scoring.
    assert bodies[0]["filters"]["is_planetary"] == {"value": False}
    assert [r.name for r in results] == ["Orbit"]


def test_query_cap_still_scores_against_full_list():
    """Commodities beyond the query cap still count in coverage scoring."""
    needed = {f"Commodity{i}": 1000 - i for i in range(stations.MAX_COMMODITIES + 2)}
    # The two smallest shortfalls fall outside the query cap.
    market = {name: 5000 for name in needed}
    st = _station("Omni", "Sol", 1.0, 10, True, market)

    queried = []

    @contextmanager
    def fake(req, timeout=0):
        body = json.loads(req.data.decode())
        queried.append(body["filters"]["market"][0]["name"])
        payload = {"results": [st]}
        yield io.BytesIO(json.dumps(payload).encode())

    with mock.patch.object(stations.urllib.request, "urlopen", fake):
        results = stations.search_stations("Sol", needed)

    assert len(queried) == stations.MAX_COMMODITIES
    # Scoring uses the full needed list, not just the queried subset.
    assert results[0].needed_total == stations.MAX_COMMODITIES + 2
    assert results[0].match_count == stations.MAX_COMMODITIES + 2
