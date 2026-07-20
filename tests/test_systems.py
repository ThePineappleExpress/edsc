import io
import json
import urllib.error
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from unittest import mock

import pytest

from edsc import spansh, systems


def _sys(name, id64, dist, xyz, body_count=None, bodies=None, nearest_pop=None):
    """Build a raw Spansh-style system record; omit optional keys when unset."""
    rec = {
        "name": name,
        "id64": id64,
        "distance": dist,
        "x": xyz[0],
        "y": xyz[1],
        "z": xyz[2],
        "updated_at": "2026-07-01 00:00:00+00",
    }
    if body_count is not None:
        rec["body_count"] = body_count
    if bodies is not None:
        rec["bodies"] = bodies
    if nearest_pop is not None:
        rec["nearest_populated_distance"] = nearest_pop
    return rec


def _body(name, type_, subtype, ls, main=False, terraforming=None):
    b = {
        "name": name,
        "type": type_,
        "subtype": subtype,
        "distance_to_arrival": ls,
        "is_main_star": main,
    }
    if terraforming is not None:
        b["terraforming_state"] = terraforming
    return b


def _agent_station(name, system, distance, with_service=True,
                   station_type="Coriolis Starport"):
    services = [{"name": "Dock"}, {"name": "Market"}]
    if with_service:
        services.append({"name": "System Colonisation"})
    return {
        "name": name,
        "system_name": system,
        "distance": distance,
        "type": station_type,
        "services": services,
    }


class _FakeSpansh:
    """urlopen stand-in serving canned pages per endpoint, recording requests."""

    def __init__(self, system_pages=None, count=None, stations_by_ref=None,
                 fail_refs=(), fail_systems=False):
        self.system_pages = system_pages or []
        self.count = count if count is not None else sum(
            len(p) for p in self.system_pages
        )
        self.stations_by_ref = stations_by_ref or {}
        self.fail_refs = set(fail_refs)
        self.fail_systems = fail_systems
        self.system_requests = []
        self.station_requests = []

    @contextmanager
    def __call__(self, req, timeout=0):
        body = json.loads(req.data.decode())
        if req.full_url == spansh.SYSTEMS_URL:
            if self.fail_systems:
                raise urllib.error.HTTPError(req.full_url, 500, "boom", {}, None)
            self.system_requests.append(body)
            page = body["page"]
            results = (
                self.system_pages[page] if page < len(self.system_pages) else []
            )
            payload = {"count": self.count, "results": results}
        else:
            self.station_requests.append(body)
            ref = body["reference_system"]
            if ref in self.fail_refs:
                raise urllib.error.URLError("agent lookup down")
            payload = {"count": 1, "results": self.stations_by_ref.get(ref, [])}
        yield io.BytesIO(json.dumps(payload).encode())


@pytest.fixture(autouse=True)
def _clear_agent_cache():
    systems._agent_cache.clear()
    systems._verify_cache.clear()
    yield
    systems._agent_cache.clear()
    systems._verify_cache.clear()


def _search(fake, reference="Sol", range_ly=100, *, raven_known=None, **kwargs):
    # Raven verification shares urllib with Spansh, so stub it out here rather than route its GETs through the Spansh fake; ``raven_known`` is the set of id64s Raven "has" (any truthy stand-in is enough), None keeps the default all-unknown Raven, leaving candidates unverified.
    def fake_fetch(id64, timeout):
        if raven_known is not None and id64 in raven_known:
            return object()
        return None

    with mock.patch.object(spansh.urllib.request, "urlopen", fake), \
            mock.patch.object(systems.raven, "fetch_system", fake_fetch):
        return systems.search_colonisation_targets(reference, range_ly, **kwargs)


#  request shape -


def test_systems_request_carries_the_colonization_filters():
    fake = _FakeSpansh([[_sys("A", 1, 5.0, (0, 0, 0), nearest_pop=4.0)]])
    _search(fake, reference="Dharragense", range_ly=42)
    body = fake.system_requests[0]
    assert body["reference_system"] == "Dharragense"
    assert body["filters"]["population"] == {"comparison": "<=>", "value": [0, 0]}
    assert body["filters"]["needs_permit"] == {"value": False}
    # Never filter the colonisation flags server-side: Spansh drops records that lack the field, and frontier systems often carry neither flag.
    assert "is_colonised" not in body["filters"]
    assert "is_being_colonised" not in body["filters"]
    assert body["filters"]["distance"] == {"min": "0", "max": "42"}
    assert body["sort"] == [{"distance": {"direction": "asc"}}]
    assert body["size"] == systems._GRAPH_PAGE_SIZE


def test_systems_request_limits_data_to_the_last_week():
    fake = _FakeSpansh([[_sys("A", 1, 5.0, (0, 0, 0), nearest_pop=4.0)]])
    _search(fake)

    freshness = fake.system_requests[0]["filters"]["updated_at"]
    assert freshness["comparison"] == "<=>"
    start, end = (datetime.fromisoformat(value) for value in freshness["value"])
    assert end - start == timedelta(days=systems.SYSTEM_DATA_MAX_AGE_DAYS)
    assert end.tzinfo == timezone.utc


def test_claimed_systems_are_excluded_client_side():
    records = [
        _sys("Free", 1, 5.0, (0, 0, 0), nearest_pop=4.0),
        {**_sys("Claimed", 2, 6.0, (5, 0, 0), nearest_pop=4.0),
         "is_colonised": True},
        {**_sys("Building", 3, 7.0, (0, 5, 0), nearest_pop=4.0),
         "is_being_colonised": True},
        {**_sys("Flagged free", 4, 8.0, (0, 0, 5), nearest_pop=4.0),
         "is_colonised": False, "is_being_colonised": False},
    ]
    fake = _FakeSpansh([records])
    search = _search(fake)
    assert {r.name for r in search.results} == {"Free", "Flagged free"}


def test_pagination_walks_pages_and_reports_truncation():
    pages = [
        [_sys("A", 1, 5.0, (0, 0, 0), nearest_pop=4.0)],
        [_sys("B", 2, 6.0, (5, 0, 0), nearest_pop=4.0)],
        [],
    ]
    fake = _FakeSpansh(pages, count=750)
    search = _search(fake)
    assert [b["page"] for b in fake.system_requests] == [0, 1, 2]
    freshness_ranges = [
        body["filters"]["updated_at"]["value"]
        for body in fake.system_requests
    ]
    assert all(value == freshness_ranges[0] for value in freshness_ranges)
    assert search.total_in_range == 750
    assert search.graph_truncated is True
    assert {r.name for r in search.results} == {"A", "B"}


def test_paging_stops_once_the_range_is_exhausted():
    # A radius holding fewer systems than the ceiling costs one request, however many pages the ceiling would allow.
    pages = [[_sys("A", 1, 5.0, (0, 0, 0), nearest_pop=4.0)]]
    fake = _FakeSpansh(pages, count=1)
    search = _search(fake)
    assert [b["page"] for b in fake.system_requests] == [0]
    assert search.graph_truncated is False


def test_paging_never_exceeds_the_row_ceiling_spansh_will_serve():
    # Spansh answers HTTP 500 for any page past its 10,000th row, so the page budget must stop there rather than walk into a guaranteed failure.
    assert systems._MAX_GRAPH_PAGES * systems._GRAPH_PAGE_SIZE == (
        systems.GRAPH_ROW_CEILING
    )
    rows = [
        _sys(f"S{i}", i, 5.0 + i, (float(i), 0, 0), nearest_pop=4.0)
        for i in range(3)
    ]
    # More systems in range than any number of pages could ever return.
    fake = _FakeSpansh([rows] * 40, count=systems.GRAPH_ROW_CEILING)
    search = _search(fake)
    assert len(fake.system_requests) == systems._MAX_GRAPH_PAGES
    assert search.graph_truncated is True


def test_a_search_that_fills_every_page_still_reports_truncation():
    # The ceiling case as Spansh actually serves it: ``count`` saturates at GRAPH_ROW_CEILING and full pages fetch exactly that many rows, so the two agree at the ceiling; that must still read as truncated -- the rows fill the budget, precisely when systems in range go unseen.
    full_page = [
        _sys(f"S{i}", i, 5.0 + i * 0.01, (float(i), 0, 0), nearest_pop=4.0)
        for i in range(systems._GRAPH_PAGE_SIZE)
    ]
    fake = _FakeSpansh(
        [full_page] * systems._MAX_GRAPH_PAGES, count=systems.GRAPH_ROW_CEILING
    )
    search = _search(fake, range_ly=300)
    fetched = systems._MAX_GRAPH_PAGES * systems._GRAPH_PAGE_SIZE
    assert fetched == search.total_in_range  # count and fetched agree...
    assert search.graph_truncated is True  # ...and it is still truncated
    # The radius asked for is not what it reached, and only covered_ly knows.
    assert search.covered_ly is not None
    assert search.covered_ly < 300


def test_search_reports_how_far_it_actually_reached():
    # Truncated: covered_ly is the furthest system fetched, not the radius asked for, because the search knows nothing past that distance.
    pages = [[_sys("A", 1, 5.0, (0, 0, 0), nearest_pop=4.0),
              _sys("B", 2, 12.5, (5, 0, 0), nearest_pop=4.0)]]
    search = _search(_FakeSpansh(pages, count=9999), range_ly=1000)
    assert search.graph_truncated is True
    assert search.covered_ly == 12.5


#  record parsing -


def test_full_record_parses_stars_bodies_and_terraformables():
    bodies = [
        _body("A", "Star", "M (Red dwarf) Star", 0.0, main=True),
        _body("A B", "Star", "T (Brown dwarf) Star", 4000.0),
        _body("A 1", "Planet", "Rocky Ice world", 350.5,
              terraforming="Terraformable"),
        _body("A 2", "Planet", "Icy body", 1200.0,
              terraforming="Not terraformable"),
    ]
    fake = _FakeSpansh(
        [[_sys("A", 1, 12.5, (1, 2, 3), body_count=9, bodies=bodies,
               nearest_pop=4.0)]]
    )
    (r,) = _search(fake).results
    assert r.distance_ly == 12.5
    assert r.star_count == 2
    assert r.body_count == 9
    assert r.known_body_count == 9  # honk total wins over the 4 scanned
    assert r.furthest_ls == 4000.0
    assert r.terraformable_count == 1
    assert r.updated_at == "2026-07-01 00:00:00+00"


def test_sparse_record_survives_with_unknowns():
    fake = _FakeSpansh([[{"name": "Test", "nearest_populated_distance": 1.0}]])
    (r,) = _search(fake).results
    assert r.star_count is None
    assert r.body_count is None
    assert r.known_body_count is None
    assert r.furthest_ls is None
    assert r.distance_ly is None
    assert r.steps == 1  # seeded by nearest_populated_distance despite no coords


#  steps BFS -


def _chain(*, gap=16.0):
    """A -> B -> C chain along x, D isolated; only A is near populated space."""
    return [
        _sys("A", 1, 10.0, (0.0, 0.0, 0.0), nearest_pop=10.0),
        _sys("B", 2, 20.0, (gap, 0.0, 0.0)),
        _sys("C", 3, 30.0, (gap * 2, 0.0, 0.0)),
        _sys("D", 4, 40.0, (500.0, 0.0, 0.0)),
    ]


def test_steps_count_bridge_colonies_along_the_chain():
    fake = _FakeSpansh([_chain()])
    search = _search(fake)
    steps = {r.name: r.steps for r in search.results}
    assert steps == {"A": 1, "B": 2, "C": 3}  # D unreachable, filtered out
    assert search.reachable == 3


def test_hop_boundary_is_inclusive_at_claim_range():
    fake = _FakeSpansh([_chain(gap=16.01)])
    search = _search(fake)
    assert {r.name for r in search.results} == {"A"}  # 16.01 Ly is one hop too far


def test_unreachable_beyond_max_steps_is_filtered():
    chain = [
        _sys("S0", 100, 5.0, (0.0, 0.0, 0.0), nearest_pop=4.0),
        *(
            _sys(f"S{i}", 100 + i, 5.0 + i, (15.0 * i, 0.0, 0.0))
            for i in range(1, systems.MAX_STEPS + 2)
        ),
    ]
    fake = _FakeSpansh([chain])
    search = _search(fake, max_results=50)
    steps = {r.name: r.steps for r in search.results}
    assert steps[f"S{systems.MAX_STEPS - 1}"] == systems.MAX_STEPS
    assert f"S{systems.MAX_STEPS}" not in steps  # would need an 11th colony
    assert all(s <= systems.MAX_STEPS for s in steps.values())


#  ranking -


def test_rank_weights_distance_by_body_count():
    records = [
        _sys("Near8", 1, 20.0, (0.0, 0.0, 0.0), body_count=8, nearest_pop=4.0),
        _sys("Far10", 2, 30.0, (5.0, 0.0, 0.0), body_count=10, nearest_pop=4.0),
        _sys("Rich40", 3, 80.0, (10.0, 0.0, 0.0), body_count=40, nearest_pop=4.0),
    ]
    fake = _FakeSpansh([records])
    search = _search(fake)
    # Similar body counts keep distance order; the 40-body outlier jumps first.
    assert [r.name for r in search.results] == ["Rich40", "Near8", "Far10"]


def test_max_results_caps_the_page_after_ranking():
    records = [
        _sys(f"S{i}", i, 10.0 + i, (float(i), 0.0, 0.0), body_count=5,
             nearest_pop=4.0)
        for i in range(5)
    ]
    fake = _FakeSpansh([records])
    search = _search(fake, max_results=2)
    assert [r.name for r in search.results] == ["S0", "S1"]
    assert search.reachable == 5


def test_filtered_search_reaches_matches_beyond_the_verification_pool():
    # The nearest systems fail the filter and the only match sits past a small verification pool; the filters must run before the pool is sliced, otherwise a proximity-only pool returns nothing for a filter a wider search would satisfy.
    records = [
        _sys("Near", 1, 10.0, (0.0, 0.0, 0.0), body_count=1, nearest_pop=4.0),
        _sys("Mid", 2, 20.0, (1.0, 0.0, 0.0), body_count=1, nearest_pop=4.0),
        _sys("Rich", 3, 80.0, (2.0, 0.0, 0.0), body_count=10, nearest_pop=4.0),
    ]
    fake = _FakeSpansh([records])
    with mock.patch.object(systems, "VERIFY_POOL_SIZE", 1):
        search = _search(
            fake, max_results=1, filters=systems.SystemFilters(min_bodies=5)
        )
    assert [r.name for r in search.results] == ["Rich"]
    assert search.reachable == 3  # the full reachable total, not just matches


def test_search_caches_an_unfiltered_pool_so_filters_can_be_loosened():
    # The overlay re-filters this pool in place, so it must hold every reachable candidate, not just the ones matching the filters the search ran with -- otherwise loosening a filter could never bring a system back.
    records = [
        _sys("Poor", 1, 10.0, (0.0, 0.0, 0.0), body_count=1, nearest_pop=4.0),
        _sys("Rich", 2, 20.0, (1.0, 0.0, 0.0), body_count=10, nearest_pop=4.0),
    ]
    fake = _FakeSpansh([records])
    search = _search(fake, filters=systems.SystemFilters(min_bodies=5))
    assert [r.name for r in search.results] == ["Rich"]  # the page is filtered
    assert sorted(r.name for r in search.pool) == ["Poor", "Rich"]  # the pool is not

    with mock.patch.object(systems, "_fill_agents"):
        loosened = systems.refilter_colonisation(search.pool, systems.SystemFilters())
    assert sorted(r.name for r in loosened.results) == ["Poor", "Rich"]


#  agent lookup -


def test_agent_is_matched_client_side_on_the_service():
    fake = _FakeSpansh(
        [[_sys("A", 1, 5.0, (0, 0, 0), nearest_pop=4.0)]],
        stations_by_ref={
            "A": [
                _agent_station("No Contact", "X", 3.0, with_service=False),
                _agent_station("Contact Hub", "Y", 12.0),
                _agent_station("Farther Hub", "Z", 14.0),
            ]
        },
    )
    (r,) = _search(fake).results
    assert r.agent is not None
    assert (r.agent.name, r.agent.system, r.agent.distance_ly) == (
        "Contact Hub", "Y", 12.0,
    )
    assert r.agent_error is False


def test_agent_none_when_no_station_offers_the_service():
    fake = _FakeSpansh(
        [[_sys("A", 1, 5.0, (0, 0, 0), nearest_pop=4.0)]],
        stations_by_ref={
            "A": [_agent_station("No Contact", "X", 3.0, with_service=False)]
        },
    )
    (r,) = _search(fake).results
    assert r.agent is None
    assert r.agent_error is False


def test_agent_request_never_uses_the_broken_services_filter():
    fake = _FakeSpansh(
        [[_sys("A", 1, 5.0, (0, 0, 0), nearest_pop=4.0)]],
        stations_by_ref={"A": []},
    )
    _search(fake)
    (body,) = fake.station_requests
    # Spansh silently ignores a services filter (any value matches everything), so it must not appear in the payload; matching happens client-side.
    assert "services" not in body["filters"]
    types = body["filters"]["type"]["value"]
    for banned in ("Settlement", "Surface Settlement", "Drake-Class Carrier",
                   "Space Construction Depot", "Planetary Construction Depot"):
        assert banned not in types
    assert "Coriolis Starport" in types
    assert "Outpost" in types


def test_agent_lookups_are_cached_per_system():
    def fake_factory():
        return _FakeSpansh(
            [[_sys("A", 1, 5.0, (0, 0, 0), nearest_pop=4.0)]],
            stations_by_ref={"A": [_agent_station("Contact Hub", "Y", 12.0)]},
        )

    fake = fake_factory()
    _search(fake)
    assert len(fake.station_requests) == 1
    fake2 = fake_factory()
    (r,) = _search(fake2).results
    assert fake2.station_requests == []  # served from the session cache
    assert r.agent is not None and r.agent.name == "Contact Hub"


def test_agent_failure_marks_only_its_row_and_is_not_cached():
    def fake_factory(fail):
        return _FakeSpansh(
            [[
                _sys("A", 1, 5.0, (0, 0, 0), nearest_pop=4.0),
                _sys("B", 2, 6.0, (5, 0, 0), nearest_pop=4.0),
            ]],
            stations_by_ref={
                "A": [_agent_station("Contact Hub", "Y", 12.0)],
                "B": [_agent_station("Other Hub", "Z", 9.0)],
            },
            fail_refs=("B",) if fail else (),
        )

    search = _search(fake_factory(fail=True))
    by_name = {r.name: r for r in search.results}
    assert by_name["A"].agent is not None
    assert by_name["A"].agent_error is False
    assert by_name["B"].agent is None
    assert by_name["B"].agent_error is True
    # The failure is not cached: a refresh retries and succeeds.
    search = _search(fake_factory(fail=False))
    by_name = {r.name: r for r in search.results}
    assert by_name["B"].agent is not None and by_name["B"].agent.name == "Other Hub"
    assert by_name["B"].agent_error is False


#  raven verification -


def test_verification_flags_by_raven_membership():
    fake = _FakeSpansh([[
        _sys("Known", 111, 5.0, (0, 0, 0), nearest_pop=4.0),
        _sys("Unknown", 222, 6.0, (0, 0, 0), nearest_pop=4.0),
    ]])
    search = _search(fake, raven_known={111})
    by_name = {r.name: r for r in search.results}
    assert by_name["Known"].verified is True
    assert by_name["Unknown"].verified is False


def test_confirmed_free_systems_outrank_unconfirmed_ones():
    # "Near" is closer, but only the farther "Far" is on Raven; even under the nearest-first strategy the confirmed system must lead.
    fake = _FakeSpansh([[
        _sys("Near", 111, 5.0, (0, 0, 0), nearest_pop=4.0),
        _sys("Far", 222, 30.0, (0, 0, 0), nearest_pop=4.0),
    ]])
    search = _search(fake, raven_known={222}, sort="nearest")
    assert [r.name for r in search.results] == ["Far", "Near"]
    assert search.results[0].verified is True


def test_confirmed_system_outside_the_page_is_promoted_into_it():
    # A confirmed candidate ranked past the display window is pulled in ahead of the unconfirmed ones the base ordering would have shown instead.
    records = [
        _sys(f"S{i}", i, float(i), (0, 0, 0), nearest_pop=4.0)
        for i in range(1, 40)
    ]
    fake = _FakeSpansh([records])
    search = _search(fake, raven_known={35}, sort="nearest", max_results=5)
    assert search.results[0].name == "S35"
    assert search.results[0].verified is True


def test_verification_left_unchecked_on_raven_error():
    fake = _FakeSpansh([[_sys("Boom", 111, 5.0, (0, 0, 0), nearest_pop=4.0)]])

    def boom(id64, timeout):
        raise systems.raven.RavenError("down")

    with mock.patch.object(spansh.urllib.request, "urlopen", fake), \
            mock.patch.object(systems.raven, "fetch_system", boom):
        search = systems.search_colonisation_targets("Sol", 100)
    # A Raven outage must not mark a real candidate as unverified.
    assert search.results[0].verified is None


def test_verification_skips_candidates_without_id64():
    raw = _sys("NoId", 111, 5.0, (0, 0, 0), nearest_pop=4.0)
    del raw["id64"]
    fake = _FakeSpansh([[raw]])
    seen = []

    def fake_fetch(id64, timeout):
        seen.append(id64)
        return None

    with mock.patch.object(spansh.urllib.request, "urlopen", fake), \
            mock.patch.object(systems.raven, "fetch_system", fake_fetch):
        search = systems.search_colonisation_targets("Sol", 100)
    assert seen == []  # no id64 -> never queried
    assert search.results[0].verified is None


def test_verification_answers_are_cached_across_searches():
    fake = _FakeSpansh([[_sys("Known", 111, 5.0, (0, 0, 0), nearest_pop=4.0)]])
    calls = []

    def fake_fetch(id64, timeout):
        calls.append(id64)
        return object()

    with mock.patch.object(spansh.urllib.request, "urlopen", fake), \
            mock.patch.object(systems.raven, "fetch_system", fake_fetch):
        systems.search_colonisation_targets("Sol", 100)
        systems.search_colonisation_targets("Sol", 100)
    assert calls == [111]  # second search reuses the cached verdict


#  errors and claimability -


def test_blank_reference_raises_before_any_request():
    with pytest.raises(systems.SystemSearchError):
        systems.search_colonisation_targets("  ", 100)


def test_systems_failure_raises_search_error():
    fake = _FakeSpansh(fail_systems=True)
    with pytest.raises(systems.SystemSearchError, match="HTTP 500"):
        _search(fake)


def test_weighted_score_spans_distance_to_body_weighted():
    rich = systems.SystemResult("Rich", distance_ly=20.0, body_count=10)
    # Zero weight is pure distance; weight 1 reproduces distance / body_count.
    assert rich.weighted_score(0.0) == 20.0
    assert rich.weighted_score(1.0) == rich.rank_score == 2.0
    # Higher weight discounts distance harder for a body-rich system.
    assert rich.weighted_score(2.0) < rich.weighted_score(1.0)


def test_colonize_sort_keys_order_by_strategy():
    near_poor = systems.SystemResult("Near", distance_ly=5.0, body_count=2)
    far_rich = systems.SystemResult("Far", distance_ly=30.0, body_count=40)
    pool = [far_rich, near_poor]

    by_nearest = sorted(pool, key=systems._colonize_sort_key("nearest", 1.0))
    assert [s.name for s in by_nearest] == ["Near", "Far"]

    by_bodies = sorted(pool, key=systems._colonize_sort_key("bodies", 1.0))
    assert [s.name for s in by_bodies] == ["Far", "Near"]

    # Balanced at a high weight lets the body-rich outlier jump the nearer one.
    by_balanced = sorted(pool, key=systems._colonize_sort_key("balanced", 3.0))
    assert by_balanced[0].name == "Far"


def test_step_sort_key_floats_fewer_steps_over_strategy():
    # A far single-step system must beat a near multi-step one: reaching the nearer system needs building two bridge colonies first.
    near_far_steps = systems.SystemResult("NearFar", distance_ly=5.0, steps=3)
    far_one_step = systems.SystemResult("FarOne", distance_ly=30.0, steps=1)
    pool = [near_far_steps, far_one_step]
    ordered = sorted(pool, key=systems._step_sort_key("nearest", 1.0))
    assert [s.name for s in ordered] == ["FarOne", "NearFar"]
    # Within one step count, the strategy still orders them.
    a = systems.SystemResult("A", distance_ly=20.0, steps=2)
    b = systems.SystemResult("B", distance_ly=8.0, steps=2)
    ordered = sorted([a, b], key=systems._step_sort_key("nearest", 1.0))
    assert [s.name for s in ordered] == ["B", "A"]


def test_verified_sort_key_orders_verified_then_steps_then_strategy():
    v_far_multi = systems.SystemResult("Vfar", distance_ly=40.0, steps=4, verified=True)
    u_near_one = systems.SystemResult("Unear", distance_ly=3.0, steps=1, verified=False)
    v_one = systems.SystemResult("Vone", distance_ly=25.0, steps=1, verified=True)
    v_two = systems.SystemResult("Vtwo", distance_ly=6.0, steps=2, verified=True)
    pool = [v_far_multi, u_near_one, v_one, v_two]
    ordered = sorted(pool, key=systems._verified_sort_key("nearest", 1.0))
    # Verified group first (single step wins inside it), then unverified.
    assert [s.name for s in ordered] == ["Vone", "Vtwo", "Vfar", "Unear"]


def test_claimable_needs_step_one_and_an_agent_in_claim_range():
    agent_close = systems.AgentStation("Hub", "Y", systems.CLAIM_RANGE_LY)
    agent_far = systems.AgentStation("Hub", "Y", systems.CLAIM_RANGE_LY + 0.01)
    assert systems.SystemResult("A", steps=1, agent=agent_close).claimable
    assert not systems.SystemResult("A", steps=1, agent=agent_far).claimable
    assert not systems.SystemResult("A", steps=2, agent=agent_close).claimable
    assert not systems.SystemResult("A", steps=1, agent=None).claimable
