import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from pydantic import ValidationError

from common import db
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
