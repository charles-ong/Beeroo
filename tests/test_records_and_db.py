import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from pydantic import ValidationError

from common import db
from common.normalise import (
    legacy_to_scraped_products,
    pack_type_for,
    sku_from_url,
)
from common.records import (
    Listing,
    PackType,
    PriceObservation,
    Retailer,
    ScrapedProduct,
)

T0 = datetime(2026, 9, 19, 7, 0, tzinfo=timezone.utc)


def listing(**kw):
    base = dict(
        retailer=Retailer.DAN_MURPHYS,
        retailer_sku="250183",
        url="https://www.danmurphys.com.au/product/250183/x",
        name="Cult IPA 355mL",
    )
    return Listing(**{**base, **kw})


def test_listing_fills_volume_and_abv_from_name():
    item = listing(name="Miller Dry 3.5% Cans 355mL")
    assert item.unit_volume_ml == 355
    assert item.abv == 3.5


def test_listing_rejects_blank_name():
    with pytest.raises(ValidationError):
        listing(name="  ")


@pytest.mark.parametrize("price", [0, -1, 99999])
def test_observation_rejects_bad_price(price):
    with pytest.raises(ValidationError):
        PriceObservation(pack_type=PackType.PACK, units=6, price=price)


@pytest.mark.parametrize(
    "url,sku",
    [
        ("https://www.danmurphys.com.au/product/250183/cult-ipa", "250183"),
        ("/product/DM_587292/san-miguel?isSponsored=true", "587292"),
        ("https://example.com/other", None),
    ],
)
def test_sku_from_url(url, sku):
    assert sku_from_url(url) == sku


def test_pack_type_mapping():
    assert pack_type_for("case", 24) is PackType.CASE
    assert pack_type_for("pack", 6) is PackType.PACK
    assert pack_type_for("each", 1) is PackType.SINGLE
    assert pack_type_for("each", 4) is PackType.PACK
    assert pack_type_for("weird", 1) is None


def test_legacy_conversion_groups_and_reports_errors():
    rows = [
        {"name": "A 330mL", "price": 71.99, "quantity": 24, "unit": "case",
         "url": "/product/DM_1/a"},
        {"name": "A 330mL", "price": 25.99, "quantity": 6, "unit": "pack",
         "url": "/product/DM_1/a"},
        {"name": "A 330mL", "price": 25.99, "quantity": 6, "unit": "pack",
         "url": "/product/DM_1/a"},  # duplicate option
        {"name": "B 330mL", "price": 10, "quantity": 1, "unit": "mystery",
         "url": "/product/2/b"},
        {"name": "C 330mL", "price": 10, "quantity": 1, "unit": "each",
         "url": "/nope"},
    ]
    products, errors = legacy_to_scraped_products(rows)
    assert len(products) == 1
    assert len(products[0].prices) == 2
    assert len(errors) == 2


def test_real_scrape_output_converts_cleanly():
    path = Path(__file__).parent.parent / "data" / "dan_murphys_beer.json"
    if not path.exists():
        pytest.skip("no local scrape output")
    rows = json.loads(path.read_text())
    products, errors = legacy_to_scraped_products(rows)
    assert errors == []
    assert len(products) > 200  # 353 rows are price options, not products
    with_volume = sum(p.listing.unit_volume_ml is not None for p in products)
    assert with_volume / len(products) > 0.9


def test_observations_append_only_on_change_or_new_day():
    conn = db.connect()
    item = listing()

    def save(price, when):
        obs = PriceObservation(
            pack_type=PackType.CASE, units=16, price=price, observed_at=when
        )
        return db.save_products(
            conn, [ScrapedProduct(listing=item, prices=[obs])], when
        )

    assert save(85.99, T0) == 1
    assert save(85.99, T0 + timedelta(hours=2)) == 0   # same day, same price
    assert save(79.99, T0 + timedelta(hours=3)) == 1   # price changed
    assert save(79.99, T0 + timedelta(days=1)) == 1    # new day

    listing_id = conn.execute("SELECT id FROM listings").fetchone()["id"]
    history = db.price_history(conn, listing_id)
    assert [r["price"] for r in history] == [85.99, 79.99, 79.99]
    assert conn.execute("SELECT COUNT(*) c FROM listings").fetchone()["c"] == 1


def test_upsert_keeps_existing_abv_when_missing():
    conn = db.connect()
    first = listing(name="Miller Dry 3.5% 355mL")
    second = listing(name="Miller Dry 355mL")
    lid = db.upsert_listing(conn, first, T0)
    assert db.upsert_listing(conn, second, T0) == lid
    row = conn.execute("SELECT abv FROM listings WHERE id = ?", (lid,)).fetchone()
    assert row["abv"] == 3.5
