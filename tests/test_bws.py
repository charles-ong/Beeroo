import json
from pathlib import Path

import pytest

from common.records import PackType, Retailer
from scrapers.bws import location_from_set_pickup, parse_bws_payload

F = Path(__file__).parent / "fixtures"


def load(name):
    return json.loads((F / name).read_text())


def series(product):
    return {
        (o.pack_type, o.units, o.member_only): o.price for o in product.prices
    }


@pytest.fixture(scope="module")
def woden():
    products, errors = parse_bws_payload(load("bws_2606_products.json"), "bws:6723")
    return {p.listing.retailer_sku: p for p in products}, errors


@pytest.fixture(scope="module")
def perth():
    products, errors = parse_bws_payload(load("bws_6000_products.json"), "bws:4261")
    return {p.listing.retailer_sku: p for p in products}, errors


def test_every_product_has_abv_and_volume(woden):
    products, _ = woden
    assert products
    assert all(p.listing.abv is not None for p in products.values())
    assert all(p.listing.unit_volume_ml for p in products.values())
    assert all(p.listing.retailer is Retailer.BWS for p in products.values())


def test_listing_fields(woden):
    listing = woden[0]["809797"].listing
    assert listing.name == "Carlton Dry Lager Bottles 330ml"
    assert listing.brand == "Carlton Dry"
    assert (listing.abv, listing.unit_volume_ml) == (4.5, 330)
    assert listing.url == "https://bws.com.au/product/809797/carlton-dry-lager-bottles-330ml"


def test_pack_sizes_prices_and_app_offer(woden):
    s = series(woden[0]["809797"])
    assert s[(PackType.SINGLE, 1, False)] == 5.5
    assert s[(PackType.PACK, 6, False)] == 22.5
    assert s[(PackType.PACK, 6, True)] == 20.0      # "on app for" -> member_only
    assert s[(PackType.CASE, 24, False)] == 58.0


def test_member_case_offer(woden):
    s = series(woden[0]["96151"])  # Sol: case $57, member $52
    assert s[(PackType.CASE, 24, False)] == 57.0
    assert s[(PackType.CASE, 24, True)] == 52.0


def test_multibuy_counts_total_units(woden):
    s = series(woden[0]["38175"])  # VB: 2 cartons of 24 for $120
    assert s[(PackType.CASE, 48, False)] == 120.0
    assert s[(PackType.CASE, 24, False)] == 65.0


def test_duplicate_offers_are_collapsed(woden):
    vb = woden[0]["38033"]  # VB 750ml: 3-pack and "3 for $22" are one offer
    keys = [(o.pack_type, o.units, o.member_only, o.price) for o in vb.prices]
    assert len(keys) == len(set(keys))


def test_unavailable_items_are_reported_not_priced(woden):
    errors = dict(woden[1])
    assert errors[47988] == "no available online prices"
    assert "47988" not in woden[0]


def test_prices_differ_by_location(woden, perth):
    a, b = woden[0], perth[0]
    common = a.keys() & b.keys()
    differing = [k for k in common if series(a[k]) != series(b[k])]
    assert len(differing) >= 5
    # Great Northern Original Lager case: $54 (Woden ACT) vs $62 (Perth WA)
    assert series(a["365986"])[(PackType.CASE, 24, False)] == 54.0
    assert series(b["365986"])[(PackType.CASE, 24, False)] == 62.0


def test_observations_carry_location_key(woden, perth):
    assert all(o.location_key == "bws:6723" for p in woden[0].values() for o in p.prices)
    assert all(o.location_key == "bws:4261" for p in perth[0].values() for o in p.prices)


def test_location_from_set_pickup():
    loc = location_from_set_pickup(load("bws_2606_set_pickup.json"))
    assert (loc.store_id, loc.store_name, loc.state, loc.postcode) == (
        "6723", "Woden", "ACT", "2606",
    )
    assert loc.location_key == "bws:6723"
    perth = location_from_set_pickup(load("bws_6000_set_pickup.json"))
    assert (perth.state, perth.postcode) == ("WA", "6000")
    assert location_from_set_pickup({}) is None


def test_a_wrong_state_in_the_store_record_is_corrected_from_its_postcode():
    # real case: BWS Melbourne (3000) was returned with AddressState "SA"
    payload = {"FulfilmentInfo": {"ClickAndCollectDetails": {
        "FulfilmentStoreID": 7699, "FulfilmentStoreName": "BWS Melbourne",
        "AddressSuburb": "Melbourne", "AddressState": "SA", "AddressPostalCode": "3000"}}}
    loc = location_from_set_pickup(payload)
    assert (loc.state, loc.postcode) == ("VIC", "3000")


def test_state_field_is_used_when_the_postcode_is_unusable():
    payload = {"FulfilmentInfo": {"ClickAndCollectDetails": {
        "FulfilmentStoreID": 1, "AddressState": "WA", "AddressPostalCode": None}}}
    assert location_from_set_pickup(payload).state == "WA"
