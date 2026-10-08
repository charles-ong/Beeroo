import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from common.records import PackType
from common.units import price_per_standard_drink
from scrapers.dan_murphys import (
    location_from_preferences,
    parse_browse_payload,
    parse_prices,
)

FIXTURE = Path(__file__).parent / "fixtures" / "dan_murphys_browse_page1.json"
NOW = datetime(2026, 10, 1, tzinfo=timezone.utc)


@pytest.fixture(scope="module")
def payload():
    return json.loads(FIXTURE.read_text())


@pytest.fixture(scope="module")
def parsed(payload):
    return parse_browse_payload(payload, "dan_murphys:1546", NOW)


@pytest.fixture(scope="module")
def by_sku(parsed):
    return {p.listing.retailer_sku: p for p in parsed[0]}


def series(product):
    return {
        (o.pack_type, o.units, o.member_only): o.price for o in product.prices
    }


def test_parses_every_product_without_errors(parsed):
    products, errors = parsed
    assert errors == []
    assert len(products) == 24


def test_every_product_has_abv_and_volume(parsed):
    products, _ = parsed
    assert all(p.listing.abv is not None for p in products)
    assert all(p.listing.unit_volume_ml for p in products)


def test_listing_fields(by_sku):
    xxxx = by_sku["6076022"].listing
    assert xxxx.name == "XXXX Gold Mid Strength Lager Beer Cans 375mL"
    assert xxxx.brand == "XXXX"
    assert xxxx.abv == 3.5
    assert xxxx.unit_volume_ml == 375
    assert xxxx.url.endswith("/product/6076022/xxxx-gold-mid-strength-lager-beer-cans-375ml")


def test_in_store_only_single_is_skipped(by_sku):
    # XXXX Gold: case (30) online, "each (in-store)" single is not purchasable
    assert series(by_sku["6076022"]) == {(PackType.CASE, 30, False): 54.95}


def test_member_offer_is_flagged_and_standard_price_kept(by_sku):
    s = series(by_sku["198988"])  # Monsuta Okinawa Supreme
    assert s[(PackType.CASE, 24, False)] == 67.99   # standard
    assert s[(PackType.CASE, 24, True)] == 58.0     # member offer
    assert s[(PackType.PACK, 6, False)] == 24.99


def test_multibuy_bottles_total_units(by_sku):
    s = series(by_sku["884167"])  # Tiger: $19 for 3 bottles (member)
    assert s[(PackType.PACK, 3, True)] == 19.0
    assert s[(PackType.SINGLE, 1, False)] == 7.99
    assert s[(PackType.CASE, 12, False)] == 62.0


def test_multibuy_packs_total_units(by_sku):
    s = series(by_sku["440975"])  # Kopparberg: $36 for 2 packs of 6
    assert s[(PackType.CASE, 12, True)] == 36.0          # 12 units = a case under the shared rule
    assert s[(PackType.PACK, 6, False)] == 22.99


def test_price_per_standard_drink_from_fixture(by_sku):
    p = by_sku["6076022"]
    obs = p.prices[0]
    value = price_per_standard_drink(
        obs.price, obs.units, p.listing.unit_volume_ml, p.listing.abv
    )
    # $54.95 / (30 x 0.375 L x 3.5% x 0.789)
    assert value == pytest.approx(1.769, abs=0.001)


def test_unrecognised_price_message_is_reported_not_dropped():
    bad = {
        "Stockcode": "1",
        "UrlFriendlyName": "x",
        "Prices": {"caseprice": {"Value": 10, "Message": "mystery"}},
        "AdditionalDetails": [],
        "PackageSize": "330ML",
        "Description": "X 330ml",
    }
    products, errors = parse_browse_payload({"Bundles": [{"Products": [bad]}]}, "k", NOW)
    assert products == []
    assert errors and "unrecognised" in errors[0][1]


def test_location_from_preferences():
    prefs = {
        "ClickAndCollectDetails": {
            "FulfilmentStoreID": "1546",
            "FulfilmentStoreName": "Thornleigh",
            "AddressSuburb": "THORNLEIGH",
            "AddressState": "NSW",
            "AddressPostalCode": "2120",
        }
    }
    loc = location_from_preferences(prefs)
    assert loc.location_key == "dan_murphys:1546"
    assert (loc.suburb, loc.state, loc.postcode) == ("Thornleigh", "NSW", "2120")
    assert location_from_preferences({}) is None


# ---- a few odd products on a page of 24 must not read as "the format changed" ------------------------------------------


def _page_with_odd_products(odd):
    import copy
    page = copy.deepcopy(json.loads(FIXTURE.read_text()))
    products = [p for b in page["Bundles"] for p in b["Products"]]
    for product in products[:odd]:
        product["Prices"]["caseprice"]["Message"] = "per slab of fun"
        product["Prices"].pop("singleprice", None)
        product["Prices"].pop("promoprice", None)
    return page, len(products)


def test_three_odd_products_among_24_still_ingest():
    from app import ingest
    from common import db
    page, n = _page_with_odd_products(3)
    body = ingest.IngestIn.model_validate({"kind": "dan_murphys_browse", "payload": page, "location": {
        "store_id": "1856", "store_name": "Frenchs Forest", "suburb": "Frenchs Forest", "state": "NSW", "postcode": "2086"}})
    result = ingest.ingest_trusted(db.connect(), body)
    assert n == 24 and result["products"] == 21 and result["skipped"] == 3


def test_a_page_where_most_products_fail_is_still_rejected_with_the_reason():
    from app import ingest
    from common import db
    page, _ = _page_with_odd_products(20)
    body = ingest.IngestIn.model_validate({"kind": "dan_murphys_browse", "payload": page, "location": {
        "store_id": "1856", "state": "NSW", "postcode": "2086"}})
    with pytest.raises(ingest.IngestError) as e:
        ingest.ingest_trusted(db.connect(), body)
    assert "format may have changed" in e.value.message
    assert "20 of 24 items unreadable" in e.value.message and "unrecognised price message" in e.value.message
