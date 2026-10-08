import json
from pathlib import Path

import pytest

from common.records import PackType, Retailer
from scrapers.liquorland import (
    apply_detail,
    location_for_site,
    parse_detail,
    parse_liquorland_payload,
    parse_suburb_search,
    site_state,
)

F = Path(__file__).parent / "fixtures"


def load(name):
    return json.loads((F / name).read_text())


def series(product):
    return {
        (o.pack_type, o.units, o.member_only): o.price for o in product.prices
    }


@pytest.fixture(scope="module")
def act():
    products, errors = parse_liquorland_payload(load("liquorland_act_products.json"))
    return {p.listing.retailer_sku: p for p in products}, errors


@pytest.fixture(scope="module")
def wa():
    products, errors = parse_liquorland_payload(load("liquorland_wa_products.json"))
    return {p.listing.retailer_sku: p for p in products}, errors


def test_site_state_and_location():
    assert site_state(load("liquorland_wa_products.json")) == "ll_wa"
    loc = location_for_site("ll_wa")
    assert loc.location_key == "liquorland:ll_wa"
    assert loc.state == "WA"


def test_parses_without_errors(act, wa):
    assert act[1] == [] and wa[1] == []
    assert len(act[0]) > 20 and len(wa[0]) > 10


def test_location_key_comes_from_site_state(act, wa):
    assert all(o.location_key == "liquorland:ll_act" for p in act[0].values() for o in p.prices)
    assert all(o.location_key == "liquorland:ll_wa" for p in wa[0].values() for o in p.prices)


def test_variants_merge_into_one_listing_with_pack_types(act):
    carlton = act[0]["473583"]  # CTN24 entry
    assert carlton.listing.retailer is Retailer.LIQUORLAND
    assert carlton.listing.name == "Carlton Dry Bottle 330mL"
    assert carlton.listing.unit_volume_ml == 330
    assert carlton.listing.url == (
        "https://www.liquorland.com.au/beer-and-cider/carlton-dry-bottle-330ml_473583"
    )
    s = series(carlton)
    assert s[(PackType.CASE, 24, False)] == 51.0  # current, not "normal" $62


def test_multibuy_callout_becomes_total_units(act):
    smithys = act[0]["8754016"]  # "3 for $15", single $7
    s = series(smithys)
    assert s[(PackType.PACK, 3, False)] == 15.0
    assert s[(PackType.SINGLE, 1, False)] == 7.0


def test_multibuy_on_cartons_counts_cartons(act):
    xxxx = series(act[0]["35927"])  # "2 for $100" on a 24 carton
    assert xxxx[(PackType.CASE, 48, False)] == 100.0
    assert xxxx[(PackType.CASE, 24, False)] == 55.0


def test_thirty_block_cans_are_a_case_under_the_shared_rule(act):
    gnb = series(act[0]["2605953"])  # 30-block cans: 12 or more units is a "case" at every retailer
    assert (PackType.CASE, 30, False) in gnb


def test_prices_differ_by_state(act, wa):
    common = act[0].keys() & wa[0].keys()
    differing = [k for k in common if series(act[0][k]) != series(wa[0][k])]
    assert len(common) >= 15
    assert len(differing) >= 10


def test_abv_missing_from_list_but_filled_by_detail(act):
    listing = next(iter(act[0].values())).listing
    assert listing.abv is None
    detail = parse_detail(load("liquorland_detail_3813708_ea.json"))
    assert detail == {"sku": "3813708", "abv": 4.0, "site_standard_drinks": 1}
    assert apply_detail(listing, detail).abv == 4.0
    assert apply_detail(listing, {"abv": None}) is listing


def test_unavailable_entries_are_reported():
    payload = {
        "debugQuery": "sitestate=ll_act",
        "products": [
            {"id": "1_ea", "name": "X 330mL", "isAvailable": False, "unitOfMeasure": "ea",
             "price": {"current": 5}, "volumeMl": 330, "productUrl": "/x_1"},
            {"id": "2_weird", "name": "Y 330mL", "isAvailable": True, "unitOfMeasure": "weird",
             "price": {"current": 5}, "volumeMl": 330, "productUrl": "/y_2"},
        ],
    }
    products, errors = parse_liquorland_payload(payload)
    assert products == []
    assert [e[0] for e in errors] == ["1_ea", "2_weird"]
    assert errors[0][1] == "unavailable" and "unrecognised" in errors[1][1]


def test_requires_location_when_response_has_none():
    with pytest.raises(ValueError):
        parse_liquorland_payload({"products": []})


def test_suburb_search():
    rows = parse_suburb_search(load("liquorland_suburb_search_2606.json"))
    assert ("Woden", "ACT", "2606") in rows


# ---- which store the visit used -----------------------------------------------------------------


def test_location_names_the_store_the_visit_picked_and_falls_back_to_state_pricing():
    from scrapers.liquorland import clean_store_name
    assert location_for_site("ll_act", "Liquorland Woden").store_name == "Liquorland Woden"
    assert location_for_site("ll_act").store_name == "Liquorland ACT (state pricing)"
    assert location_for_site("ll_act", "  ").store_name == "Liquorland ACT (state pricing)"
    assert location_for_site("ll_act", "Woden").location_key == "liquorland:ll_act"      # the key never changes
    assert clean_store_name("  Wo\nden <b>Plaza</b>\t") == "Wo den b Plaza /b"
    assert len(clean_store_name("x" * 500)) == 80 and clean_store_name(None) is None


def test_ingest_uses_the_store_name_sent_with_the_page():
    import json as _json
    from app import ingest
    from common import db
    page = _json.loads((Path(__file__).parent / "fixtures" / "liquorland_act_products.json").read_text())
    conn = db.connect()
    body = ingest.IngestIn.model_validate({"kind": "liquorland_products", "payload": {**page, "store_name": "Liquorland Woden"}})
    ingest.ingest_trusted(conn, body)
    assert conn.execute("SELECT store_name FROM locations").fetchone()[0] == "Liquorland Woden"
    ingest.ingest_trusted(conn, ingest.IngestIn.model_validate({"kind": "liquorland_products", "payload": page}))
    assert conn.execute("SELECT store_name FROM locations").fetchone()[0] == "Liquorland ACT (state pricing)"   # no name sent: placeholder
