from edsc import commodities


def test_canonical_strips_token_and_lowercases():
    assert commodities.canonical_name("$aluminium_name;") == "aluminium"
    # Casing is inconsistent in the game (contributions vs depots).
    assert commodities.canonical_name("$Aluminium_name;") == "aluminium"
    assert commodities.canonical_name("aluminium") == "aluminium"
    assert commodities.canonical_name("agriculturalmedicines") == "agriculturalmedicines"


def test_canonical_handles_empty():
    assert commodities.canonical_name("") == ""
    assert commodities.canonical_name(None) == ""


def test_depot_and_cargo_names_share_a_key():
    # This equality is the whole point: it lets us join needs against cargo.
    assert commodities.canonical_name("$agriculturalmedicines_name;") == (
        commodities.canonical_name("agriculturalmedicines")
    )


def test_display_name_registration_and_fallback():
    key = commodities.register_display_name("$biowaste_name;", "Biowaste")
    assert key == "biowaste"
    assert commodities.display_name("biowaste") == "Biowaste"
    # Unknown key falls back to a title-cased id.
    assert commodities.display_name("someunknownthing") == "Someunknownthing"
