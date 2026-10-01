import pytest

from common.units import (
    parse_abv,
    parse_volume_ml,
    price_per_standard_drink,
    standard_drinks,
)


@pytest.mark.parametrize(
    "name,expected",
    [
        ("Mountain Culture Cult IPA 355mL", 355),
        ("Hahn Ultra Zero Carb Bottles 330mL", 330),
        ("Some Lager 1.25L", 1250),
        ("Carlton Draught 24 x 375mL", 375),
        ("Mystery Beer", None),
        (None, None),
    ],
)
def test_parse_volume(name, expected):
    assert parse_volume_ml(name) == expected


@pytest.mark.parametrize(
    "name,expected",
    [
        ("Miller Dry 3.5% Cans 355mL", 3.5),
        ("Hahn Ultra 0% 330mL", 0.0),
        ("Cult IPA 355mL", None),
        ("Fake 150% Beer", None),
    ],
)
def test_parse_abv(name, expected):
    assert parse_abv(name) == expected


def test_standard_drinks_known_value():
    # 375 mL at 4.6% -> 1.36 standard drinks (common AU reference value)
    assert standard_drinks(375, 4.6) == pytest.approx(1.361, abs=0.001)


def test_standard_drinks_missing_inputs():
    assert standard_drinks(None, 4.6) is None
    assert standard_drinks(375, None) is None


def test_price_per_standard_drink():
    # $60 case of 24 x 375 mL at 4.6% => 24 * 1.361 = 32.66 drinks
    assert price_per_standard_drink(60, 24, 375, 4.6) == pytest.approx(
        1.837, abs=0.001
    )


def test_price_per_standard_drink_zero_abv():
    assert price_per_standard_drink(20, 6, 330, 0.0) is None


# ---- one pack rule for every retailer ---------------------------------------


@pytest.mark.parametrize("units,expected", [
    (1, "single"), (2, "pack"), (4, "pack"), (6, "pack"), (11, "pack"),
    (12, "case"), (16, "case"), (24, "case"), (30, "case"), (48, "case"),
])
def test_pack_type_is_decided_by_unit_count(units, expected):
    from common.records import pack_type_for_units
    assert pack_type_for_units(units).value == expected


def test_all_three_retailers_classify_a_30_pack_the_same_way():
    """The pack filter must mean the same thing at BWS, Liquorland and Dan Murphy's."""
    import json
    from datetime import datetime, timezone
    from pathlib import Path
    from scrapers import bws, endeavour, liquorland
    now = datetime(2026, 10, 1, tzinfo=timezone.utc)
    f = Path(__file__).parent / "fixtures"
    ll, _ = liquorland.parse_liquorland_payload(json.loads((f / "liquorland_act_products.json").read_text()), observed_at=now)
    dm, _ = endeavour.parse_browse_payload(json.loads((f / "dan_murphys_browse_page1.json").read_text()), "k", now)
    bw, _ = bws.parse_bws_payload(json.loads((f / "bws_2606_products.json").read_text()), "k", now)
    for products in (ll, dm, bw):
        for p in products:
            for o in p.prices:
                if o.units >= 12:
                    assert o.pack_type.value == "case", (p.listing.name, o.units, o.pack_type)
                elif o.units > 1:
                    assert o.pack_type.value == "pack", (p.listing.name, o.units, o.pack_type)
