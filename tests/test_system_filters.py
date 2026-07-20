"""Tests for the colonize result filters and the ring-composition lookup."""

# SPDX-License-Identifier: GPL-3.0-or-later

from unittest import mock

from edsc import spansh, systems


def _body(type_, subtype, *, main=False, terra=""):
    return systems.BodyInfo(
        name="b",
        type=type_,
        subtype=subtype,
        distance_to_arrival=1.0,
        is_main_star=main,
        terraforming_state=terra,
    )


def _sys(name, **kw):
    return systems.SystemResult(name=name, **kw)


def test_numeric_filters_gate_bodies_hops_and_stars():
    s = _sys(
        "S",
        body_count=8,
        steps=2,
        bodies=[_body("Star", "M (Red dwarf) Star", main=True), _body("Star", "K (..) Star")],
    )
    assert systems.passes_filters(s, systems.SystemFilters())
    assert not systems.passes_filters(s, systems.SystemFilters(min_bodies=9))
    assert systems.passes_filters(s, systems.SystemFilters(min_bodies=8))
    assert not systems.passes_filters(s, systems.SystemFilters(max_hops=1))
    assert systems.passes_filters(s, systems.SystemFilters(max_hops=2))
    assert not systems.passes_filters(s, systems.SystemFilters(min_stars=3))
    assert systems.passes_filters(s, systems.SystemFilters(min_stars=2))


def test_boolean_filters_require_terraformable_claimable_verified():
    plain = _sys("P", steps=2, verified=False, bodies=[_body("Planet", "Icy body")])
    rich = _sys(
        "R",
        steps=1,
        verified=True,
        bodies=[_body("Planet", "Water world", terra="Terraformable")],
    )
    assert not systems.passes_filters(plain, systems.SystemFilters(terraformable_only=True))
    assert systems.passes_filters(rich, systems.SystemFilters(terraformable_only=True))
    assert not systems.passes_filters(plain, systems.SystemFilters(claimable_only=True))
    assert systems.passes_filters(rich, systems.SystemFilters(claimable_only=True))
    assert not systems.passes_filters(plain, systems.SystemFilters(verified_only=True))
    assert systems.passes_filters(rich, systems.SystemFilters(verified_only=True))


def test_body_type_toggles_are_anded_and_match_subtype():
    s = _sys(
        "S",
        steps=1,
        bodies=[_body("Planet", "Earth-like world"), _body("Planet", "Water world")],
    )
    assert systems.passes_filters(s, systems.SystemFilters(body_types=("ELW",)))
    assert systems.passes_filters(s, systems.SystemFilters(body_types=("ELW", "WW")))
    # AND: a type the system lacks fails the whole filter.
    assert not systems.passes_filters(s, systems.SystemFilters(body_types=("ELW", "AW")))


def test_scoopable_checks_the_primary_and_excludes_brown_dwarfs():
    scoopable = _sys("A", steps=1, bodies=[_body("Star", "G (White-Yellow) Star", main=True)])
    brown = _sys("B", steps=1, bodies=[_body("Star", "L (Brown dwarf) Star", main=True)])
    assert systems.passes_filters(scoopable, systems.SystemFilters(star_types=("scoop",)))
    assert not systems.passes_filters(brown, systems.SystemFilters(star_types=("scoop",)))


def test_exotic_star_toggles_match_any_star_in_the_system():
    s = _sys(
        "S",
        steps=1,
        bodies=[_body("Star", "K (..) Star", main=True), _body("Star", "Neutron Star")],
    )
    assert systems.passes_filters(s, systems.SystemFilters(star_types=("NS",)))
    assert not systems.passes_filters(s, systems.SystemFilters(star_types=("BH",)))


def test_ring_filter_uses_the_supplied_map_and_ands():
    s = _sys("S", steps=1, bodies=[])
    ring_map = {"S": {"Metallic", "Icy"}}
    f = systems.SystemFilters(ring_types=("Metallic",))
    assert systems.passes_filters(s, f, ring_map)
    assert not systems.passes_filters(s, systems.SystemFilters(ring_types=("Rocky",)), ring_map)
    # A system absent from the map counts as having no rings.
    assert not systems.passes_filters(s, f, {})


def test_refilter_sorts_verified_first_then_slices():
    a = _sys("A", distance_ly=30, body_count=5, steps=2, verified=True)
    b = _sys("B", distance_ly=5, body_count=5, steps=2, verified=False)
    with mock.patch.object(systems, "_fill_verification"), mock.patch.object(
        systems, "_fill_agents"
    ):
        out = systems.refilter_colonisation(
            [b, a], systems.SystemFilters(), max_results=1
        )
    # verified floats above nearer-but-unverified
    assert [s.name for s in out.results] == ["A"]


def test_refilter_fetches_rings_only_when_a_ring_filter_is_active():
    pool = [_sys("S", steps=1, bodies=[])]
    with mock.patch.object(systems, "get_ring_map") as ring, mock.patch.object(
        systems, "_fill_verification"
    ), mock.patch.object(systems, "_fill_agents"):
        systems.refilter_colonisation(pool, systems.SystemFilters())
        ring.assert_not_called()
        ring.return_value = {"S": {"Metallic"}}
        result = systems.refilter_colonisation(
            pool, systems.SystemFilters(ring_types=("Metallic",))
        )
        ring.assert_called_once()
    assert result.matched == 1
    assert [s.name for s in result.results] == ["S"]


def test_refilter_reports_ring_error_and_leaves_rings_unfiltered():
    pool = [_sys("S", steps=1, bodies=[])]
    with mock.patch.object(
        systems, "get_ring_map", side_effect=systems.SystemSearchError("boom")
    ), mock.patch.object(systems, "_fill_verification"), mock.patch.object(
        systems, "_fill_agents"
    ):
        result = systems.refilter_colonisation(
            pool, systems.SystemFilters(ring_types=("Metallic",))
        )
    assert result.ring_error is True
    assert [s.name for s in result.results] == ["S"]  # not dropped for lacking rings


def test_refilter_bounds_the_ring_lookup_to_the_ranked_head():
    pool = [_sys(f"S{i}", steps=1, distance_ly=float(i)) for i in range(400)]
    with mock.patch.object(
        systems, "get_ring_map", return_value={}
    ) as ring, mock.patch.object(
        systems, "_fill_verification"
    ), mock.patch.object(systems, "_fill_agents"):
        systems.refilter_colonisation(
            pool, systems.SystemFilters(ring_types=("Rocky",)), sort="nearest"
        )
    asked = ring.call_args.args[0]
    assert len(asked) == systems.RING_LOOKUP_LIMIT
    assert asked[0] == "S0"  # the nearest, i.e. the head of the ranking


def test_fetch_ring_map_batches_by_system_name_and_reports_ringless_systems():
    names = [f"S{i}" for i in range(systems._RING_BATCH + 1)]
    page = {
        "count": 2,
        "results": [
            {"system_name": "S0", "rings": [{"type": "Metallic"}, {"type": "Icy"}]},
            {"system_name": "S0", "rings": [{"type": "Rocky"}]},
        ],
    }
    with mock.patch.object(spansh, "post", return_value=page) as post:
        out = systems.fetch_ring_map(names)
    assert out["S0"] == {"Metallic", "Icy", "Rocky"}
    # Every requested name is answered; a system with no ringed bodies is a definite "no rings", not a gap.
    assert out["S1"] == set()
    assert set(out) == set(names)
    # One request per batch, and systems are named rather than swept by radius (which silently truncates far short of the search radius).
    assert post.call_count == 2
    body = post.call_args_list[0].args[1]
    assert post.call_args_list[0].args[0] == spansh.BODIES_URL
    assert body["filters"]["rings"] == [{"name": "Icy"}]
    assert body["filters"]["system_name"]["value"] == names[: systems._RING_BATCH]
    assert "distance" not in body["filters"]


def test_get_ring_map_caches_per_system_and_fetches_only_unseen():
    systems._ring_map_cache.clear()
    with mock.patch.object(
        systems, "fetch_ring_map", return_value={"A": {"Icy"}}
    ) as fetch:
        assert systems.get_ring_map(["A"]) == {"A": {"Icy"}}
        systems.get_ring_map(["A"])
        fetch.assert_called_once()  # served from cache

        fetch.return_value = {"B": set()}
        out = systems.get_ring_map(["A", "B"])
        assert fetch.call_args.args[0] == ["B"]  # only the unseen system
    assert out == {"A": {"Icy"}, "B": set()}
