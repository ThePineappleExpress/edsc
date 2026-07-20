import io
import json
import zlib
from collections import Counter
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from unittest import mock

from edsc import stations


def _station(name, system, distance, arrival, large, market, planetary=False,
             station_type="Coriolis Starport"):
    """Build a raw Spansh-style station dict with a full market array."""
    return {
        "name": name,
        "system_name": system,
        "distance": distance,
        "distance_to_arrival": arrival,
        "has_large_pad": large,
        "is_planetary": planetary,
        "type": station_type,
        "market_id": zlib.crc32(name.encode()) % 1_000_000,
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


def test_nearest_sort_leads_with_distance_over_coverage():
    with mock.patch.object(stations.urllib.request, "urlopen", _fake_urlopen):
        results = stations.search_stations(
            "Sol", ["Aluminium", "Steel"], sort="nearest"
        )
    # Beta (3 Ly, 1 match) now outranks Alpha (5 Ly, 2 matches).
    assert [r.name for r in results] == ["Beta", "Alpha"]


def test_fresh_sort_leads_with_market_recency():
    stale = _station("Stale", "Sol", 1.0, 10, True, {"Aluminium": 5000})
    stale["market_updated_at"] = "2020-01-01T00:00:00Z"
    fresh = _station("Fresh", "Wolf 359", 9.0, 10, True, {"Aluminium": 5000})
    fresh["market_updated_at"] = "2026-07-11T00:00:00Z"

    @contextmanager
    def fake(req, timeout=0):
        yield io.BytesIO(json.dumps({"results": [stale, fresh]}).encode())

    with mock.patch.object(stations.urllib.request, "urlopen", fake):
        results = stations.search_stations("Sol", ["Aluminium"], sort="fresh")
    # The most recently updated market leads regardless of distance.
    assert [r.name for r in results] == ["Fresh", "Stale"]


def test_range_ly_caps_the_search_radius_in_the_request():
    seen: list[dict] = []

    @contextmanager
    def fake(req, timeout=0):
        seen.append(json.loads(req.data.decode())["filters"])
        yield io.BytesIO(json.dumps({"results": []}).encode())

    with mock.patch.object(stations.urllib.request, "urlopen", fake):
        stations.search_stations("Sol", ["Aluminium"], range_ly=150)
    assert seen and all(
        f.get("distance") == {"min": "0", "max": "150"} for f in seen
    )


def test_unlimited_range_sends_no_distance_filter():
    seen: list[dict] = []

    @contextmanager
    def fake(req, timeout=0):
        seen.append(json.loads(req.data.decode())["filters"])
        yield io.BytesIO(json.dumps({"results": []}).encode())

    with mock.patch.object(stations.urllib.request, "urlopen", fake):
        stations.search_stations("Sol", ["Aluminium"], range_ly=0)
    assert seen and all("distance" not in f for f in seen)


def test_missing_lists_the_unstocked_needs():
    with mock.patch.object(stations.urllib.request, "urlopen", _fake_urlopen):
        results = stations.search_stations("Sol", ["Aluminium", "Steel"])
    alpha, beta = results
    assert alpha.missing == []
    assert beta.missing == ["Steel"]


def test_demand_by_name_records_the_requested_tonnage():
    with mock.patch.object(stations.urllib.request, "urlopen", _fake_urlopen):
        results = stations.search_stations("Sol", {"Aluminium": 3000, "Steel": 500})
    alpha, beta = results
    # Every result carries the full request so tooltips can show each commodity's stocked share; amount-less lists record 0.
    assert alpha.demand_by_name == {"Aluminium": 3000, "Steel": 500}
    assert beta.demand_by_name == {"Aluminium": 3000, "Steel": 500}


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
    # Steel needs 5000 -> threshold is the 100 t floor, 40 t doesn't count; Aluminium needs 200 -> threshold 100, 500 t counts.
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
    # Discovery always fills every category for reusable local filtering; the compatibility option only filters the returned pool.
    categories = {
        frozenset(body["filters"]["type"]["value"]) for body in bodies
    }
    assert categories == {
        frozenset(stations._ORBITAL_TYPES),
        frozenset(stations._PLANETARY_TYPES),
        frozenset(stations._CARRIER_TYPES),
    }
    assert [r.name for r in results] == ["Orbit"]


def test_planetary_type_quirk_excluded_when_planets_off():
    """Spansh sometimes reports surface types with is_planetary false; the planets toggle must catch them by type too."""
    quirky = _station("Ground", "Sol", 1.0, 10, True, {"Steel": 5000},
                      planetary=False, station_type="Planetary Outpost")
    orbital = _station("Orbit", "Sol", 2.0, 20, True, {"Steel": 5000})

    @contextmanager
    def fake(req, timeout=0):
        payload = {"results": [quirky, orbital]}
        yield io.BytesIO(json.dumps(payload).encode())

    with mock.patch.object(stations.urllib.request, "urlopen", fake):
        results = stations.search_stations(
            "Sol", ["Steel"], include_planetary=False
        )
    assert [r.name for r in results] == ["Orbit"]


def test_owner_is_faction_for_stations_and_vanity_name_for_carriers():
    port = _station("Alpha", "Sol", 5.0, 100, True, {"Steel": 5000})
    port["controlling_minor_faction"] = "Sol Workers' Party"
    named = _station("T9Z-94L", "Sol", 1.0, 10, True, {"Steel": 5000},
                     station_type="Drake-Class Carrier")
    # Spansh reports the placeholder faction "FleetCarrier" for carriers; the vanity name is the only real proprietor information it has.
    named["controlling_minor_faction"] = "FleetCarrier"
    named["carrier_name"] = "PEQUOD"
    bare = _station("B4R-E77", "Sol", 2.0, 10, True, {"Steel": 5000},
                    station_type="Drake-Class Carrier")
    bare["controlling_minor_faction"] = "FleetCarrier"

    @contextmanager
    def fake(req, timeout=0):
        payload = {"results": [port, named, bare]}
        yield io.BytesIO(json.dumps(payload).encode())

    with mock.patch.object(stations.urllib.request, "urlopen", fake):
        results = stations.search_stations("Sol", ["Steel"])
    owners = {r.name: r.owner for r in results}
    assert owners == {
        "Alpha": "Sol Workers' Party",
        "T9Z-94L": "PEQUOD",
        "B4R-E77": "",  # placeholder faction never leaks through
    }


def test_carriers_included_by_default():
    carrier = _station("Hauler", "Sol", 1.0, 10, True, {"Steel": 5000},
                       station_type="Drake-Class Carrier")

    @contextmanager
    def fake(req, timeout=0):
        payload = {"results": [carrier]}
        yield io.BytesIO(json.dumps(payload).encode())

    with mock.patch.object(stations.urllib.request, "urlopen", fake):
        results = stations.search_stations("Sol", ["Steel"])
    assert [r.name for r in results] == ["Hauler"]
    assert results[0].is_carrier


def test_carriers_excluded_when_toggled_off():
    carrier = _station("Hauler", "Sol", 1.0, 10, True, {"Steel": 5000},
                       station_type="Drake-Class Carrier")
    orbital = _station("Orbit", "Sol", 2.0, 20, True, {"Steel": 5000})
    bodies = []

    @contextmanager
    def fake(req, timeout=0):
        bodies.append(json.loads(req.data.decode()))
        payload = {"results": [carrier, orbital]}
        yield io.BytesIO(json.dumps(payload).encode())

    with mock.patch.object(stations.urllib.request, "urlopen", fake):
        results = stations.search_stations(
            "Sol", ["Steel"], include_carriers=False
        )
    categories = {
        frozenset(body["filters"]["type"]["value"]) for body in bodies
    }
    assert frozenset(stations._CARRIER_TYPES) in categories
    assert [r.name for r in results] == ["Orbit"]


def test_search_always_fetches_each_category_explicitly():
    orbital = _station("Orbit", "Sol", 2.0, 20, True, {"Steel": 5000})
    bodies = []

    @contextmanager
    def fake(req, timeout=0):
        bodies.append(json.loads(req.data.decode()))
        yield io.BytesIO(json.dumps({"results": [orbital]}).encode())

    with mock.patch.object(stations.urllib.request, "urlopen", fake):
        stations.search_stations("Sol", ["Steel"])
    categories = {
        frozenset(body["filters"]["type"]["value"]) for body in bodies
    }
    assert categories == {
        frozenset(stations._ORBITAL_TYPES),
        frozenset(stations._PLANETARY_TYPES),
        frozenset(stations._CARRIER_TYPES),
    }


def test_one_stop_station_outranks_nearer_partial_stockists():
    """A distant full-coverage station beats nearby partial ones: per-commodity pages only see the nearest stockists of each commodity, so the combined AND query surfaces the one-stop station even when it's hundreds of light years (a carrier jump or two) away."""
    near_steel = _station("Steelworks", "Near", 5.0, 10, True, {"Steel": 9000})
    near_alu = _station("Foundry", "Near", 6.0, 10, True, {"Aluminium": 9000})
    one_stop = _station("Everything", "Bubble", 420.0, 50, True,
                        {"Steel": 9000, "Aluminium": 9000})

    @contextmanager
    def fake(req, timeout=0):
        body = json.loads(req.data.decode())
        market = body["filters"]["market"]
        if len(market) > 1:
            results = [one_stop]  # only the AND query finds it
        elif market[0]["name"] == "Steel":
            results = [near_steel]
        else:
            results = [near_alu]
        yield io.BytesIO(json.dumps({"results": results}).encode())

    with mock.patch.object(stations.urllib.request, "urlopen", fake):
        results = stations.search_stations(
            "Sol", {"Steel": 5000, "Aluminium": 5000}
        )
    assert results[0].name == "Everything"
    assert results[0].match_count == 2


def test_single_commodity_skips_combined_query():
    bodies = []

    @contextmanager
    def fake(req, timeout=0):
        bodies.append(json.loads(req.data.decode()))
        yield io.BytesIO(json.dumps({"results": []}).encode())

    with mock.patch.object(stations.urllib.request, "urlopen", fake):
        stations.search_stations("Sol", {"Steel": 5000})
    # One request per category, with no redundant combined query.
    assert len(bodies) == 3
    assert all(
        [m["name"] for m in body["filters"]["market"]] == ["Steel"]
        for body in bodies
    )
    assert all(body["size"] == stations.RESULTS_PER_CATEGORY for body in bodies)


def test_carrier_saturated_page_still_finds_stations():
    """A region where the nearest stockists are wall-to-wall carriers: fetching the nearest page and dropping carriers afterwards would discard every candidate, so pushing the type filter into the query lets Spansh skip straight to the real stations further out."""
    carriers = [
        _station(f"K{i}X-{i}{i}Z", "Staging", 1.0 + i, 10, True, {"Steel": 9000},
                 station_type="Drake-Class Carrier")
        for i in range(25)
    ]
    port = _station("Far Port", "Bubble", 400.0, 100, True, {"Steel": 9000})

    @contextmanager
    def fake(req, timeout=0):
        body = json.loads(req.data.decode())
        # Spansh honours the type filter; without it the page is all carriers.
        results = [port] if "type" in body["filters"] else carriers
        yield io.BytesIO(json.dumps({"results": results}).encode())

    with mock.patch.object(stations.urllib.request, "urlopen", fake):
        results = stations.search_stations(
            "Sol", {"Steel": 5000}, include_carriers=False
        )
    assert [r.name for r in results] == ["Far Port"]


def test_every_commodity_is_queried_and_combined_optimisation_is_capped():
    """No needed item may disappear merely because the list exceeds the cap."""
    needed = {f"Commodity{i}": 1000 - i for i in range(stations.MAX_COMMODITIES + 2)}
    market = dict.fromkeys(needed, 5000)
    st = _station("Omni", "Sol", 1.0, 10, True, market)

    queried = []

    @contextmanager
    def fake(req, timeout=0):
        body = json.loads(req.data.decode())
        queried.append([m["name"] for m in body["filters"]["market"]])
        payload = {"results": [st]}
        yield io.BytesIO(json.dumps(payload).encode())

    with mock.patch.object(stations.urllib.request, "urlopen", fake):
        results = stations.search_stations("Sol", needed)

    singles = [q[0] for q in queried if len(q) == 1]
    combined = [q for q in queried if len(q) > 1]
    # Each commodity is discovered independently in all three categories.
    assert Counter(singles) == Counter(dict.fromkeys(needed, 3))
    # Only the optional one-stop AND query is capped, once per category.
    assert len(combined) == 3
    assert all(len(query) == stations.MAX_COMMODITIES for query in combined)
    assert results[0].needed_total == stations.MAX_COMMODITIES + 2
    assert results[0].match_count == stations.MAX_COMMODITIES + 2


def test_coverage_full_when_supply_meets_demand():
    """A matched station with enough of each commodity reports 100% coverage."""
    st = _station("Full", "Sol", 1.0, 10, True,
                  {"Steel": 5000, "Aluminium": 5000})

    @contextmanager
    def fake(req, timeout=0):
        payload = {"results": [st]}
        yield io.BytesIO(json.dumps(payload).encode())

    with mock.patch.object(stations.urllib.request, "urlopen", fake):
        results = stations.search_stations("Sol", {"Steel": 200, "Aluminium": 300})
    assert results and results[0].coverage == 1.0


def test_coverage_reflects_tonnage_shortfall():
    """Matched on breadth but short on tonnage -> coverage below 100%."""
    st = _station("Short", "Sol", 1.0, 10, True,
                  {"Steel": 5000, "Aluminium": 8000})

    @contextmanager
    def fake(req, timeout=0):
        payload = {"results": [st]}
        yield io.BytesIO(json.dumps(payload).encode())

    with mock.patch.object(stations.urllib.request, "urlopen", fake):
        results = stations.search_stations(
            "Sol", {"Steel": 100000, "Aluminium": 100000}
        )
    top = results[0]
    # Stocks both (breadth) but only 13000 of 200000 t demanded.
    assert top.match_count == 2
    assert top.covered_tons == 13000
    assert top.demand_tons == 200000
    assert abs(top.coverage - 13000 / 200000) < 1e-9


def test_amountless_list_reports_full_coverage():
    """Amount-less searches have no tonnage, so any match reads as 100%."""
    st = _station("Listy", "Sol", 1.0, 10, True, {"Steel": 40})

    @contextmanager
    def fake(req, timeout=0):
        payload = {"results": [st]}
        yield io.BytesIO(json.dumps(payload).encode())

    with mock.patch.object(stations.urllib.request, "urlopen", fake):
        results = stations.search_stations("Sol", ["Steel"])
    assert results and results[0].coverage == 1.0


def test_residual_demand_after_top_station():
    """What a supplementary search should look for: tonnage the best station can't supply, including commodities it doesn't stock at all."""
    st = _station("Top", "Sol", 1.0, 10, True,
                  {"Steel": 5000, "Aluminium": 40, "Copper": 0})

    @contextmanager
    def fake(req, timeout=0):
        yield io.BytesIO(json.dumps({"results": [st]}).encode())

    needed = {"Steel": 8000, "Aluminium": 30, "Copper": 100}
    with mock.patch.object(stations.urllib.request, "urlopen", fake):
        results = stations.search_stations("Sol", needed)
    top = results[0]
    # Supply is recorded per needed commodity, even below the match threshold.
    assert top.supply_by_name == {"Steel": 5000, "Aluminium": 40}
    residual = stations.residual_demand(needed, top)
    # Steel is 3000 t short; Aluminium's 40 t covers the 30 needed; Copper isn't stocked at all.
    assert residual == {"Steel": 3000, "Copper": 100}


def test_residual_demand_amountless_list():
    """Amount-less needs are residual only when not stocked at all."""
    st = _station("Top", "Sol", 1.0, 10, True, {"Steel": 40})

    @contextmanager
    def fake(req, timeout=0):
        yield io.BytesIO(json.dumps({"results": [st]}).encode())

    with mock.patch.object(stations.urllib.request, "urlopen", fake):
        results = stations.search_stations("Sol", ["Steel"])
    residual = stations.residual_demand(["Steel", "Copper"], results[0])
    assert residual == {"Copper": 0}


def test_stations_covering_missing_picks_complementary_stops():
    """The follow-up list is a minimal greedy plan, not duplicate alternatives."""
    top = _station("Top", "Sol", 1.0, 10, True, {"Steel": 5000, "Aluminium": 5000})
    filler = _station("Filler", "Wolf", 2.0, 20, True, {"Copper": 5000, "Gold": 5000})
    partial = _station("Partial", "Ross", 3.0, 30, True, {"Copper": 5000})

    @contextmanager
    def fake(req, timeout=0):
        body = json.loads(req.data.decode())
        name = body["filters"]["market"][0]["name"]
        results = []
        if name in ("Steel", "Aluminium"):
            results.append(top)
        if name in ("Copper", "Gold"):
            results.append(filler)
        if name == "Copper":
            results.append(partial)
        yield io.BytesIO(json.dumps({"results": results}).encode())

    needed = {"Steel": 100, "Aluminium": 100, "Copper": 100, "Gold": 100}
    with mock.patch.object(stations.urllib.request, "urlopen", fake):
        results = stations.search_stations("Sol", needed)
    follow_up = stations.stations_covering_missing(results, needed)
    # Filler covers the entire Copper+Gold gap, so Partial is redundant.
    assert [s.name for s in follow_up] == ["Filler"]


def test_results_are_capped_at_ten_per_category():
    def category_stations(types):
        if types == stations._CARRIER_TYPES:
            return [
                _station(
                    f"C{i}", "Carriers", i + 1, 10, True, {"Steel": 5000},
                    station_type="Drake-Class Carrier",
                )
                for i in range(15)
            ]
        if types == stations._PLANETARY_TYPES:
            return [
                _station(
                    f"P{i}", "Surface", i + 1, 10, True, {"Steel": 5000},
                    planetary=True, station_type="Planetary Port",
                )
                for i in range(15)
            ]
        return [
            _station(f"O{i}", "Orbit", i + 1, 10, True, {"Steel": 5000})
            for i in range(15)
        ]

    @contextmanager
    def fake(req, timeout=0):
        body = json.loads(req.data.decode())
        raw = category_stations(body["filters"]["type"]["value"])
        yield io.BytesIO(json.dumps({"results": raw}).encode())

    with mock.patch.object(stations.urllib.request, "urlopen", fake):
        results = stations.search_stations("Sol", ["Steel"])

    assert sum(not s.is_planetary and not s.is_carrier for s in results) == 10
    assert sum(s.is_planetary and not s.is_carrier for s in results) == 10
    assert sum(s.is_carrier for s in results) == 10


def test_category_filters_add_to_the_orbital_baseline():
    common = {
        "system": "Sol",
        "distance_ly": 1.0,
        "arrival_ls": 10.0,
        "has_large_pad": True,
        "market_updated_at": "2026-07-11T08:00:00Z",
    }
    orbital = stations.StationResult(
        name="Orbit",
        is_planetary=False,
        station_type="Coriolis Starport",
        is_carrier=False,
        **common,
    )
    planetary = stations.StationResult(
        name="Surface",
        is_planetary=True,
        station_type="Planetary Port",
        is_carrier=False,
        **common,
    )
    carrier = stations.StationResult(
        name="K1T-00A",
        is_planetary=False,
        station_type="Drake-Class Carrier",
        is_carrier=True,
        **common,
    )
    pool = [orbital, planetary, carrier]

    def names(planets, carriers):
        return [
            station.name
            for station in stations.filter_stations(
                pool,
                include_planetary=planets,
                include_carriers=carriers,
            )
        ]

    assert names(False, False) == ["Orbit"]
    assert names(True, False) == ["Orbit", "Surface"]
    assert names(False, True) == ["Orbit", "K1T-00A"]
    assert names(True, True) == ["Orbit", "Surface", "K1T-00A"]


def test_mixed_result_cap_keeps_each_available_category_visible():
    common = {
        "system": "Sol",
        "arrival_ls": 10.0,
        "has_large_pad": True,
        "market_updated_at": "2026-07-11T08:00:00Z",
    }
    orbitals = [
        stations.StationResult(
            name=f"Orbit {index}",
            distance_ly=float(index),
            is_planetary=False,
            station_type="Coriolis Starport",
            is_carrier=False,
            **common,
        )
        for index in range(10)
    ]
    planetary = stations.StationResult(
        name="Surface",
        distance_ly=20.0,
        is_planetary=True,
        station_type="Planetary Port",
        is_carrier=False,
        **common,
    )
    carrier = stations.StationResult(
        name="K1T-00A",
        distance_ly=30.0,
        is_planetary=False,
        station_type="Drake-Class Carrier",
        is_carrier=True,
        **common,
    )

    mixed = stations.limit_mixed_results(
        [*orbitals, planetary, carrier], limit=10
    )

    assert len(mixed) == 10
    assert mixed[0] is orbitals[0]
    assert planetary in mixed
    assert carrier in mixed


def test_supplementary_candidates_keep_planetary_and_carrier_alternatives():
    common = {
        "system": "Sol",
        "arrival_ls": 10.0,
        "has_large_pad": True,
        "market_updated_at": "2026-07-11T08:00:00Z",
        "needed_total": 2,
        "demand_by_name": {"Steel": 100, "Aluminium": 100},
    }
    primary = stations.StationResult(
        name="Orbit",
        distance_ly=1.0,
        is_planetary=False,
        station_type="Coriolis Starport",
        is_carrier=False,
        matched=["Steel"],
        supply_by_name={"Steel": 100},
        **common,
    )
    planetary = stations.StationResult(
        name="Surface",
        distance_ly=2.0,
        is_planetary=True,
        station_type="Planetary Port",
        is_carrier=False,
        matched=["Aluminium"],
        supply_by_name={"Aluminium": 100},
        **common,
    )
    carrier = stations.StationResult(
        name="K1T-00A",
        distance_ly=3.0,
        is_planetary=False,
        station_type="Drake-Class Carrier",
        is_carrier=True,
        matched=["Aluminium"],
        supply_by_name={"Aluminium": 100},
        **common,
    )
    needed = {"Steel": 100, "Aluminium": 100}

    candidates = stations.supplementary_candidates(
        [primary, planetary, carrier], needed, primary
    )

    assert candidates == [planetary, carrier]
    # The greedy completeness plan may need only one stop; that must not alter which valid alternatives are offered in the supplementary table.
    assert stations.supplementary_stations(
        [primary, planetary, carrier], needed, primary
    ) == [planetary]


def test_recent_only_is_a_24_hour_api_prefilter():
    bodies = []

    @contextmanager
    def fake(req, timeout=0):
        bodies.append(json.loads(req.data.decode()))
        yield io.BytesIO(json.dumps({"results": []}).encode())

    with mock.patch.object(stations.urllib.request, "urlopen", fake):
        stations.search_stations("Sol", ["Steel"], recent_only=True)

    ranges = [body["filters"]["market_updated_at"] for body in bodies]
    assert len(ranges) == 3
    assert all(item["comparison"] == "<=>" for item in ranges)
    assert all(item["value"] == ranges[0]["value"] for item in ranges)
    start, end = (
        datetime.fromisoformat(value) for value in ranges[0]["value"]
    )
    assert end - start == timedelta(hours=24)
    assert end.tzinfo == timezone.utc


def test_planetary_and_carrier_results_complete_orbital_shortfall():
    orbital = _station("Orbit", "Sol", 1.0, 10, True, {"Steel": 5000})
    planetary = _station(
        "Surface", "Sol", 2.0, 10, True, {"Aluminium": 5000},
        planetary=True, station_type="Planetary Port",
    )
    carrier = _station(
        "K1T-00A", "Sol", 3.0, 10, True, {"Copper": 5000},
        station_type="Drake-Class Carrier",
    )

    @contextmanager
    def fake(req, timeout=0):
        body = json.loads(req.data.decode())
        market = body["filters"]["market"]
        types = body["filters"]["type"]["value"]
        found = []
        if len(market) == 1:
            commodity = market[0]["name"]
            if types == stations._ORBITAL_TYPES and commodity == "Steel":
                found = [orbital]
            elif types == stations._PLANETARY_TYPES and commodity == "Aluminium":
                found = [planetary]
            elif types == stations._CARRIER_TYPES and commodity == "Copper":
                found = [carrier]
        yield io.BytesIO(json.dumps({"results": found}).encode())

    needed = {"Steel": 100, "Aluminium": 100, "Copper": 100}
    with mock.patch.object(stations.urllib.request, "urlopen", fake):
        results = stations.search_stations("Sol", needed)

    plan = [results[0], *stations.stations_covering_missing(results, needed)]
    assert [station.name for station in plan] == ["Orbit", "Surface", "K1T-00A"]
    assert stations.remaining_demand(needed, plan) == {}

