import copy
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

import pytest

import sys
from app import queries
from common import db
from common.records import Listing, Retailer, ScrapedProduct, clean_rating
from scrapers import bws, dan_murphys_dom as dom, endeavour

sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))
F = Path(__file__).parent / "fixtures"
NOW = datetime(2026, 10, 8, tzinfo=timezone.utc)
K = "k"


@pytest.mark.parametrize("given,expected", [
    ((4.362, 163), (4.36, 163)),
    ((0, 0), (None, 0)),               # no reviews: no rating, not zero stars
    ((4.5, 0), (None, 0)),
    ((0, 12), (None, 12)),             # a zero rating is nonsense
    ((7, 5), (None, 5)),
    ((None, None), (None, None)),
    (("4.2", "37"), (4.2, 37)),
    ((4.0, None), (4.0, None)),
    (("x", "y"), (None, None)),
    ((4.0, -3), (4.0, None)),
])
def test_clean_rating(given, expected):
    assert clean_rating(*given) == expected


# ---- parsers ----------------------------------------------------------------------------------


def test_bws_reads_rating_and_review_count():
    payload = json.loads((F / "bws_2606_products.json").read_text())
    products, _ = bws.parse_bws_payload(payload, "bws:1", NOW)
    rated = [p for p in products if p.listing.rating]
    assert rated and all(0 < p.listing.rating <= 5 and p.listing.review_count > 0 for p in rated)
    # Carlton Dry's variants report 163/164/166 reviews: the most-reviewed figure is used
    assert any(p.listing.review_count == 166 and p.listing.rating == 4.37 for p in rated)


def test_bws_slimmed_payload_keeps_ratings():
    payload = json.loads((F / "bws_2606_products.json").read_text())
    full, _ = bws.parse_bws_payload(payload, "bws:1", NOW)
    slim, _ = bws.parse_bws_payload(bws.minimal_payload(payload), "bws:1", NOW)
    ratings = lambda ps: {p.listing.retailer_sku: (p.listing.rating, p.listing.review_count) for p in ps}
    assert ratings(slim) == {k: v for k, v in ratings(full).items() if k in ratings(slim)}


def test_dan_murphys_json_reads_rating_and_review_count():
    payload = json.loads((F / "dan_murphys_browse_page1.json").read_text())
    product = payload["Bundles"][0]["Products"][0]
    product["OverallRating"], product["NumberOfReviews"] = 4.2, 37
    products, _ = endeavour.parse_browse_payload(payload, K, NOW)
    first = next(p for p in products if p.listing.retailer_sku == str(product["Stockcode"]))
    assert (first.listing.rating, first.listing.review_count) == (4.2, 37)
    assert all(p.listing.rating is None and p.listing.review_count == 0 for p in products if p is not first)


def card(rating, *lines):
    return {"href": "/product/DM_1/test-beer-330ml", "rating": rating, "lines": list(lines)}


def test_dan_murphys_cards_read_stars_and_the_review_count_in_either_text_layout():
    split = dom.parse_card(card(4.54, "(116", "REVIEW", "REVIEWS", ")", "San Miguel", "Pale Pilsen 330mL", "$25.99", "pack (6)"), K)
    merged = dom.parse_card(card(4.54, " (116 REVIEWS )", "San Miguel", "Pale Pilsen 330mL", "$25.99 pack (6)"), K)
    for p in (split, merged):
        assert (p.listing.rating, p.listing.review_count) == (4.54, 116)
    none = dom.parse_card(card(0, "(0 REVIEWS)", "Foo", "Bar 330mL", "$25.99 pack (6)"), K)
    assert (none.listing.rating, none.listing.review_count) == (None, 0)
    unknown = dom.parse_card(card(None, "Foo", "Bar 330mL", "$25.99 pack (6)"), K)
    assert (unknown.listing.rating, unknown.listing.review_count) == (None, None)


def test_real_saved_cards_carry_review_counts():
    cards = json.loads((F / "dan_murphys_cards.json").read_text())
    products, _ = dom.parse_cards_payload(cards, K)
    counted = [p for p in products if p.listing.review_count]
    assert len(counted) > 20 and all(p.listing.rating is None for p in counted)     # fixture predates star capture


# ---- storage --------------------------------------------------------------------------------------


def listing(**kw):
    base = dict(retailer=Retailer.BWS, retailer_sku="1", url="https://x/1", name="Foo Lager Cans 375ml")
    return Listing(**{**base, **kw})


def test_a_database_from_before_ratings_is_upgraded_in_place(tmp_path):
    path = tmp_path / "old.sqlite3"
    old = sqlite3.connect(path)
    old.executescript("""
        CREATE TABLE listings (id INTEGER PRIMARY KEY, retailer TEXT NOT NULL, retailer_sku TEXT NOT NULL, url TEXT NOT NULL,
            name TEXT NOT NULL, brand TEXT, category TEXT, abv REAL, unit_volume_ml REAL, product_id INTEGER,
            match_confidence REAL, first_seen TEXT NOT NULL, last_seen TEXT NOT NULL, UNIQUE (retailer, retailer_sku));
        INSERT INTO listings (retailer, retailer_sku, url, name, first_seen, last_seen) VALUES ('bws', '9', 'u', 'Old Beer', 'x', 'x');
    """)
    old.commit()
    old.close()
    conn = db.connect(str(path))
    cols = {r["name"] for r in conn.execute("PRAGMA table_info(listings)")}
    assert {"rating", "review_count"} <= cols
    assert conn.execute("SELECT name, rating FROM listings").fetchone()["name"] == "Old Beer"
    db.connect(str(path))                                         # and doing it twice is fine


def test_ratings_are_stored_updated_and_not_wiped_by_a_source_that_has_none():
    conn = db.connect()
    lid = db.upsert_listing(conn, listing(rating=4.0, review_count=10), NOW)
    get = lambda: tuple(conn.execute("SELECT rating, review_count FROM listings WHERE id = ?", (lid,)).fetchone())
    assert get() == (4.0, 10)
    db.upsert_listing(conn, listing(rating=4.4, review_count=25), NOW)
    assert get() == (4.4, 25)                                     # newer figures replace
    db.upsert_listing(conn, listing(), NOW)                       # e.g. Liquorland: says nothing
    assert get() == (4.4, 25)
    db.upsert_listing(conn, listing(rating=None, review_count=0), NOW)
    assert get() == (None, 0)                                     # reviews removed: now unrated
    assert db.load_listings(conn)[0][2].review_count == 0


# ---- combining ratings --------------------------------------------------------------------------------


def test_combined_rating_is_weighted_by_review_count():
    assert queries.combined_rating([{"rating": 4.0, "review_count": 100}, {"rating": 5.0, "review_count": 100}]) == (4.5, 200)
    assert queries.combined_rating([{"rating": 4.0, "review_count": 300}, {"rating": 5.0, "review_count": 100}]) == (4.25, 400)
    assert queries.combined_rating([{"rating": 3.0, "review_count": 10}, {"rating": None, "review_count": None}]) == (3.0, 10)
    assert queries.combined_rating([{"rating": None, "review_count": 0}]) == (None, 0)
    assert queries.combined_rating([{"rating": 4.0}]) == (4.0, 0)                 # count unknown still shows


def seeded(tmp_path):
    from common.records import Location, PackType, PriceObservation
    from common.pipeline import run_matching
    conn = db.connect(str(tmp_path / "r.sqlite3"))
    for retailer, store, rating, reviews in ((Retailer.BWS, "bws:1", 4.0, 300), (Retailer.DAN_MURPHYS, "dan_murphys:1", 5.0, 100),
                                             (Retailer.LIQUORLAND, "liquorland:ll_act", None, None)):
        loc = Location(retailer=retailer, store_id=store.split(":")[1], state="ACT", postcode="2600")
        db.upsert_location(conn, loc)
        item = listing(retailer=retailer, retailer_sku=retailer.value, url="https://x/" + retailer.value,
                       name="Foo Lager Cans 375ml", rating=rating, review_count=reviews)
        obs = PriceObservation(pack_type=PackType.PACK, units=6, price=20.0, location_key=loc.location_key, observed_at=NOW)
        db.save_products(conn, [ScrapedProduct(listing=item, prices=[obs])], NOW)
    run_matching(conn)
    return conn


def test_compare_gives_each_retailer_its_rating_and_the_product_a_pooled_one(tmp_path):
    conn = seeded(tmp_path)
    [p] = queries.compare(conn, "ACT", now=NOW)["products"]
    assert (p["rating"], p["review_count"]) == (4.25, 400)
    r = p["retailers"]
    assert (r["bws"]["rating"], r["bws"]["review_count"]) == (4.0, 300)
    assert (r["dan_murphys"]["rating"], r["dan_murphys"]["review_count"]) == (5.0, 100)
    assert r["liquorland"]["rating"] is None and r["liquorland"]["review_count"] is None


def test_filters_do_not_change_a_products_rating(tmp_path):
    conn = seeded(tmp_path)
    [p] = queries.compare(conn, "ACT", retailers=["liquorland"], min_units=6, now=NOW)["products"]
    assert (p["rating"], p["review_count"]) == (4.25, 400)           # reviews are of the beer, not of the filter


def test_detail_and_static_export_carry_the_ratings(tmp_path):
    import export_static
    conn = seeded(tmp_path)
    pid = queries.compare(conn, "ACT", now=NOW)["products"][0]["id"]
    assert queries.product_detail(conn, pid, "ACT", NOW)["product"]["rating"] == 4.25
    export_static.export(conn, tmp_path / "site", ["ACT"], NOW)
    exported = json.loads((tmp_path / "site" / "data" / "ACT" / "products.json").read_text())["products"][0]
    assert exported["rating"] == 4.25 and exported["retailers"]["bws"]["review_count"] == 300


# ---- sorting by rating ----------------------------------------------------------------------


def test_rating_score_pulls_thin_ratings_toward_the_prior():
    five_from_one = queries.rating_score({"rating": 5.0, "review_count": 1})
    solid = queries.rating_score({"rating": 4.6, "review_count": 300})
    assert solid > five_from_one                                          # one 5-star review doesn't win
    assert queries.rating_score({"rating": 4.0, "review_count": 3}) == 4.0   # the prior itself is unmoved
    assert queries.rating_score({"rating": 5.0, "review_count": 1000}) > 4.95
    assert queries.rating_score({"rating": 3.0, "review_count": None}) > 3.0       # unknown count: counted as one review


def seeded_ratings(tmp_path, rows):
    from common.pipeline import run_matching
    from common.records import Location, PackType, PriceObservation
    conn = db.connect(str(tmp_path / "s.sqlite3"))
    loc = Location(retailer=Retailer.BWS, store_id="1", state="ACT", postcode="2600")
    db.upsert_location(conn, loc)
    for i, (name, rating, reviews) in enumerate(rows):
        item = listing(retailer_sku=str(i), url=f"https://x/{i}", name=name, rating=rating, review_count=reviews)
        obs = PriceObservation(pack_type=PackType.PACK, units=6, price=20.0, location_key=loc.location_key, observed_at=NOW)
        db.save_products(conn, [ScrapedProduct(listing=item, prices=[obs])], NOW)
    run_matching(conn)
    return conn


def test_sort_by_rating_ranks_by_score_with_unrated_last(tmp_path):
    conn = seeded_ratings(tmp_path, [
        ("Alpha Pale Ale Cans 375ml", 5.0, 1), ("Bravo Stout Cans 375ml", 4.6, 300), ("Charlie IPA Cans 375ml", 4.1, 50),
        ("Delta Lager Cans 375ml", None, None), ("Echo Porter Cans 375ml", 4.6, 300)])
    names = [p["name"] for p in queries.compare(conn, "ACT", sort="rating", now=NOW)["products"]]
    assert names == ["Bravo Stout Cans", "Echo Porter Cans", "Alpha Pale Ale Cans", "Charlie IPA Cans", "Delta Lager Cans"]
    assert queries.compare(conn, "ACT", sort="rating", now=NOW)["products"][0]["rating"] == 4.6


def test_rating_is_an_accepted_sort_over_http(tmp_path):
    from fastapi.testclient import TestClient
    from app.main import create_app
    seeded_ratings(tmp_path, [("Alpha Pale Ale Cans 375ml", 4.0, 10)]).close()
    client = TestClient(create_app(str(tmp_path / "s.sqlite3")))
    assert client.get("/api/compare", params={"state": "ACT", "sort": "rating"}).status_code == 200
