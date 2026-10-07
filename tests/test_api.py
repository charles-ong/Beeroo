import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import sys
sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))
import ingest  # noqa: E402

from app import queries  # noqa: E402
from app.main import create_app  # noqa: E402
from common import db  # noqa: E402

F = Path(__file__).parent / "fixtures"
WHEN = "2026-10-01T04:00:00+00:00"
NOW = datetime(2026, 10, 3, tzinfo=timezone.utc)


@pytest.fixture(scope="module")
def db_path(tmp_path_factory):
    path = tmp_path_factory.mktemp("db") / "t.sqlite3"
    conn = db.connect(str(path))
    for name, pick in (("bws_2606", "bws_2606_set_pickup.json"), ("bws_6000", "bws_6000_set_pickup.json")):
        loc = ingest.bws.location_from_set_pickup(json.loads((F / pick).read_text()))
        ingest.ingest_files(conn, "bws", [F / f"{name}_products.json"], loc, WHEN)
    ingest.ingest_files(conn, "liquorland", [F / "liquorland_act_products.json", F / "liquorland_wa_products.json"], None, WHEN)
    ingest.run_matching(conn)
    conn.close()
    return str(path)


@pytest.fixture(scope="module")
def client(db_path):
    return TestClient(create_app(db_path))


@pytest.fixture()
def conn(db_path):
    c = db.connect(db_path)
    yield c
    c.close()


def test_locations_pick_same_state(client):
    act = client.get("/api/locations", params={"state": "ACT"}).json()
    assert act["state"] == "ACT"
    assert act["retailers"]["bws"]["store_name"] == "Woden"
    assert act["retailers"]["liquorland"]["location_key"] == "liquorland:ll_act"

    wa = client.get("/api/locations", params={"state": "WA"}).json()
    assert wa["retailers"]["bws"]["store_name"] == "Murray Street"
    assert wa["retailers"]["liquorland"]["location_key"] == "liquorland:ll_wa"


def test_no_cross_state_fallback_a_missing_retailer_is_simply_absent(client):
    """Showing another state's prices (or comparing retailers across states) would be wrong."""
    vic = client.get("/api/locations", params={"state": "VIC"}).json()
    assert all(loc is None for loc in vic["retailers"].values())              # nothing loaded for Victoria
    assert vic["notices"] == [f"No {n} prices for Victoria yet." for n in ("Dan Murphy's", "BWS", "Liquorland")]
    assert "fallback" not in json.dumps(vic)
    data = client.get("/api/compare", params={"state": "VIC"}).json()
    assert data["products"] == [] and data["meta"]["total"] == 0               # NOT another state's products

    act = client.get("/api/locations", params={"state": "ACT"}).json()          # partial coverage inside a state
    assert act["retailers"]["bws"] and act["retailers"]["liquorland"] and act["retailers"]["dan_murphys"] is None
    assert act["notices"] == ["No Dan Murphy's prices for Australian Capital Territory yet."]


def test_prices_for_one_state_never_leak_into_another(client):
    act_ids = {p["id"] for p in client.get("/api/compare", params={"state": "ACT", "limit": 200}).json()["products"]}
    wa = client.get("/api/compare", params={"state": "WA", "limit": 200}).json()
    for p in wa["products"]:
        for retailer, entry in p["retailers"].items():
            assert wa["locations"]["retailers"][retailer]["state"] == "WA"
    assert act_ids and client.get("/api/compare", params={"state": "TAS"}).json()["meta"]["total"] == 0


@pytest.mark.parametrize("bad", ["", "XX", "abc", "2606", "N S W", "ACT;DROP"])
def test_invalid_state_is_400(client, bad):
    assert client.get("/api/compare", params={"state": bad}).status_code == 400
    assert client.get("/api/locations", params={"state": bad}).status_code == 400


def test_state_is_required_and_case_insensitive(client):
    assert client.get("/api/compare").status_code == 422
    lower = client.get("/api/compare", params={"state": "act"}).json()
    assert lower["locations"]["state"] == "ACT"


def test_states_endpoint_lists_every_state_with_its_pricing_postcode(client):
    states = client.get("/api/states").json()["states"]
    assert [s["code"] for s in states] == ["ACT", "NSW", "NT", "QLD", "SA", "TAS", "VIC", "WA"]   # by name
    by = {s["code"]: s for s in states}
    assert by["NSW"] == {"code": "NSW", "name": "New South Wales", "postcode": "2100"}
    assert all(len(s["postcode"]) == 4 for s in states)


def test_compare_prices_differ_by_state(client):
    def case_price(pc):
        data = client.get("/api/compare", params={"state": pc, "q": "great northern original lager 330"}).json()
        prod = next(p for p in data["products"] if "Original Lager" in p["name"] and "Bottle" in p["name"] or p["retailers"].get("bws"))
        opt = next(o for o in prod["retailers"]["bws"]["options"] if o["label"] == "Case of 24")
        return opt["price"]

    assert case_price("ACT") == 54.0
    assert case_price("WA") == 62.0


def test_value_sort_puts_unknown_abv_last_and_is_ascending(client):
    products = client.get("/api/compare", params={"state": "ACT", "limit": 200}).json()["products"]
    values = [p["min_price_per_standard_drink"] for p in products]
    known = [v for v in values if v is not None]
    assert known == sorted(known)
    first_none = values.index(None) if None in values else len(values)
    assert all(v is None for v in values[first_none:])


def test_cheapest_retailer_is_flagged_only_when_two_compete(client):
    products = client.get("/api/compare", params={"state": "ACT", "limit": 200}).json()["products"]
    multi = [p for p in products if len(p["retailers"]) >= 2 and p["best_value_retailer"]]
    assert multi
    for p in multi:
        winner = p["best_value_retailer"]
        mine = p["retailers"][winner]["best_value"]["price_per_standard_drink"]
        others = [e["best_value"]["price_per_standard_drink"] for r, e in p["retailers"].items()
                  if r != winner and e["best_value"]]
        assert all(mine <= o for o in others)
    singles = [p for p in products if len(p["retailers"]) == 1]
    assert all(p["best_value_retailer"] is None for p in singles)


def options_of(products):
    return [o for p in products for e in p["retailers"].values() for o in e["options"]]


def test_member_offers_are_included_by_default_and_can_be_hidden(client):
    on = client.get("/api/compare", params={"state": "ACT", "limit": 200}).json()["products"]
    off = client.get("/api/compare", params={"state": "ACT", "limit": 200, "include_member": False}).json()["products"]
    assert any(o["member_only"] for o in options_of(on))
    assert not any(o["member_only"] for o in options_of(off))
    assert len(options_of(on)) > len(options_of(off))


def test_hiding_member_offers_changes_the_best_price_too(client):
    on = {p["id"]: p for p in client.get("/api/compare", params={"state": "ACT", "limit": 200}).json()["products"]}
    off = {p["id"]: p for p in client.get("/api/compare", params={"state": "ACT", "limit": 200, "include_member": False}).json()["products"]}
    shown = [i for i in off if on[i]["min_unit_price"] != off[i]["min_unit_price"]]
    assert shown                                      # a member price was the cheapest for some products
    for i in shown:
        assert on[i]["min_unit_price"] < off[i]["min_unit_price"]


def test_quantity_range_keeps_only_matching_pack_sizes_and_recomputes(client):
    data = client.get("/api/compare", params={"state": "ACT", "min_units": 12, "limit": 200}).json()
    assert data["products"] and all(o["units"] >= 12 for o in options_of(data["products"]))
    capped = client.get("/api/compare", params={"state": "ACT", "max_units": 6, "limit": 200}).json()
    assert capped["products"] and all(o["units"] <= 6 for o in options_of(capped["products"]))
    exact = client.get("/api/compare", params={"state": "ACT", "min_units": 24, "max_units": 24, "limit": 200}).json()["products"]
    assert exact and {o["units"] for o in options_of(exact)} == {24}
    for p in exact:                                   # the "best" fields only consider what is still shown
        for e in p["retailers"].values():
            assert e["cheapest_unit"]["units"] == 24
    none = client.get("/api/compare", params={"state": "ACT", "min_units": 40, "max_units": 30}).json()
    assert none["products"] == [] and none["meta"]["total"] == 0


def test_filter_by_one_or_more_retailers(client):
    both = client.get("/api/compare", params={"state": "ACT", "limit": 200}).json()["meta"]["total"]
    bws = client.get("/api/compare", params={"state": "ACT", "retailer": "bws", "limit": 200}).json()
    assert 0 < bws["meta"]["total"] <= both and all("bws" in p["retailers"] for p in bws["products"])
    either = client.get("/api/compare", params={"state": "ACT", "retailer": ["bws", "liquorland"], "limit": 200}).json()
    assert either["meta"]["total"] == both               # ACT has only those two retailers


def test_filter_by_type_and_facets_list_what_exists(client):
    data = client.get("/api/compare", params={"state": "ACT", "limit": 1}).json()
    facets = data["meta"]["facets"]
    assert facets["units"] == sorted(facets["units"]) and 1 in facets["units"]
    types = {t["type"]: t["count"] for t in facets["types"]}
    assert "Lager" in types and sum(types.values()) == data["meta"]["total"]
    pick = next(t for t in types if t != "Lager")
    only = client.get("/api/compare", params={"state": "ACT", "type": pick, "limit": 200}).json()
    assert only["meta"]["total"] == types[pick] and {p["type"] for p in only["products"]} == {pick}
    two = client.get("/api/compare", params={"state": "ACT", "type": [pick, "Lager"], "limit": 200}).json()
    assert two["meta"]["total"] == types[pick] + types["Lager"]


def test_titles_have_no_pack_size_or_volume_but_search_still_finds_them(client):
    products = client.get("/api/compare", params={"state": "ACT", "limit": 200}).json()["products"]
    import re
    assert not any(re.search(r"\d\s*x\s*\d+\s*ml|\b\d+\s*ml\b", p["name"], re.I) for p in products)
    assert all(p["raw_name"] for p in products) and any(p["raw_name"] != p["name"] for p in products)
    some = next(p for p in products if p["unit_volume_ml"])
    found = client.get("/api/compare", params={"state": "ACT", "q": f"{int(some['unit_volume_ml'])}ml", "limit": 200}).json()
    assert some["id"] in {p["id"] for p in found["products"]}


def test_abv_filter(client):
    strong = client.get("/api/compare", params={"state": "ACT", "min_abv": 5, "limit": 200}).json()["products"]
    assert strong and all(p["abv"] >= 5 for p in strong)
    mid = client.get("/api/compare", params={"state": "ACT", "min_abv": 3.5, "max_abv": 4.5, "limit": 200}).json()["products"]
    assert mid and all(3.5 <= p["abv"] <= 4.5 for p in mid)


def test_abv_source_is_exposed_for_borrowed_abv(conn):
    data = queries.compare(conn, "ACT", limit=500)
    borrowed = [p for p in data["products"] if "liquorland" in p["retailers"] and p["abv_source"] == "bws"]
    assert borrowed
    p = borrowed[0]
    assert p["retailers"]["liquorland"]["best_value"]["price_per_standard_drink"] is not None


def test_liquorland_only_unknown_abv_has_no_value(conn):
    data = queries.compare(conn, "ACT", limit=500)
    unknown = [p for p in data["products"] if p["abv"] is None]
    assert unknown
    for p in unknown:
        for e in p["retailers"].values():
            assert e["best_value"] is None
            assert all(o["price_per_standard_drink"] is None for o in e["options"])


def test_stale_flag(conn):
    data = queries.compare(conn, "ACT", limit=500, now=NOW + timedelta(days=30))
    assert all(e["stale"] for p in data["products"] for e in p["retailers"].values())
    fresh = queries.compare(conn, "ACT", limit=500, now=NOW)
    assert not any(e["stale"] for p in fresh["products"] for e in p["retailers"].values())


def test_bad_sort_type_and_retailer_are_400(client):
    assert client.get("/api/compare", params={"state": "ACT", "sort": "nope"}).status_code == 400
    assert client.get("/api/compare", params={"state": "ACT", "type": "Mead"}).status_code == 400
    assert client.get("/api/compare", params={"state": "ACT", "retailer": "x"}).status_code == 400


def test_product_detail_and_404(client):
    pid = client.get("/api/compare", params={"state": "ACT"}).json()["products"][0]["id"]
    detail = client.get(f"/api/products/{pid}", params={"state": "ACT"})
    assert detail.status_code == 200
    body = detail.json()
    assert body["history"] and not body["demo"]
    series = next(iter(next(iter(body["history"].values())).values()))
    assert series["n"] == 1 and series["signal"] == "not_enough_history"
    assert client.get("/api/products/999999", params={"state": "ACT"}).status_code == 404


def test_history_signal(conn):
    from app.queries import _signal
    assert _signal([5, 5]) == "not_enough_history"
    assert _signal([6, 6, 5]) == "lowest_seen"
    assert _signal([5, 5, 6, 5.9]) in {"usual", "above_usual"}
    assert _signal([5, 6, 6, 6, 5.5]) == "below_usual"
    assert _signal([5, 5, 5, 5, 6.5]) == "above_usual"


def test_index_page_served(client):
    assert client.get("/").status_code == 200


def test_demo_db_is_flagged_and_history_is_simulated_only_in_past(tmp_path):
    sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))
    import build_demo_db

    out = tmp_path / "demo.sqlite3"
    conn = db.connect(str(out))
    build_demo_db.load_real(conn, raw=False)
    real = conn.execute("SELECT MAX(observed_at) m FROM price_observations").fetchone()["m"]
    n = build_demo_db.simulate_history(conn)
    db.set_meta(conn, "demo", "1")
    conn.close()
    assert n > 100

    client = TestClient(create_app(str(out)))
    data = client.get("/api/compare", params={"state": "ACT"}).json()
    assert data["meta"]["demo"] is True
    pid = data["products"][0]["id"]
    detail = client.get(f"/api/products/{pid}", params={"state": "ACT"}).json()
    assert detail["demo"] is True
    series = next(iter(next(iter(detail["history"].values())).values()))
    assert series["n"] == 9  # 1 real + 8 simulated weekly points
    assert series["points"][-1]["t"] <= real  # nothing simulated in the future
    assert series["points"][-1]["price"] > 0


def test_latest_price_is_by_observation_time_not_insert_order(tmp_path):
    """Backfilled (older) rows inserted later must not override newer prices."""
    path = tmp_path / "t.sqlite3"
    conn = db.connect(str(path))
    loc = ingest.bws.location_from_set_pickup(json.loads((F / "bws_2606_set_pickup.json").read_text()))
    ingest.ingest_files(conn, "bws", [F / "bws_2606_products.json"], loc, WHEN)
    ingest.run_matching(conn)
    row = conn.execute("SELECT * FROM price_observations ORDER BY id LIMIT 1").fetchone()
    conn.execute(
        "INSERT INTO price_observations (listing_id, location_key, pack_type, units, member_only, price, observed_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        (row["listing_id"], row["location_key"], row["pack_type"], row["units"], row["member_only"],
         row["price"] + 50, "2026-08-01T00:00:00+00:00"),
    )
    conn.commit()
    data = queries.compare(conn, "ACT", limit=500, now=NOW)
    prices = {o["price"] for p in data["products"] for e in p["retailers"].values() for o in e["options"]}
    assert row["price"] + 50 not in prices
    assert not any(e["stale"] for p in data["products"] for e in p["retailers"].values())


# ---- a pack that stops appearing must stop being shown ---------------------------


def test_a_series_not_seen_on_the_listings_latest_scrape_day_is_hidden(tmp_path):
    from common.records import Listing, PackType, PriceObservation, Retailer, ScrapedProduct
    conn = db.connect(str(tmp_path / "stale.sqlite3"))
    listing = Listing(retailer=Retailer.BWS, retailer_sku="1", url="https://bws.com.au/p/1", name="Test Lager Cans 375ml", abv=4.5)
    other = Listing(retailer=Retailer.BWS, retailer_sku="2", url="https://bws.com.au/p/2", name="Other Lager Cans 375ml", abv=4.5)
    loc = ingest.bws.location_from_set_pickup(json.loads((F / "bws_2606_set_pickup.json").read_text()))
    db.upsert_location(conn, loc)
    day1 = datetime(2026, 10, 1, 4, tzinfo=timezone.utc)
    day2 = day1 + timedelta(days=1)

    def obs(pack, units, price, when):
        return PriceObservation(pack_type=pack, units=units, price=price, location_key=loc.location_key, observed_at=when)

    # day 1: the wrongly-parsed "10 for $5" and the right single/pack; day 2: parser fixed, only right rows
    db.save_products(conn, [ScrapedProduct(listing=listing, prices=[
        obs(PackType.PACK, 10, 5.0, day1), obs(PackType.SINGLE, 1, 5.0, day1)])], day1)
    db.save_products(conn, [ScrapedProduct(listing=listing, prices=[
        obs(PackType.SINGLE, 1, 5.0, day2), obs(PackType.PACK, 10, 25.0, day2)])], day2)
    # a different listing seen only on day 1 (e.g. one skipped scrape) is NOT hidden by that
    db.save_products(conn, [ScrapedProduct(listing=other, prices=[obs(PackType.SINGLE, 1, 6.0, day1)])], day1)
    ingest.run_matching(conn)

    got = queries.compare(conn, "ACT", limit=50, now=day2)
    by_name = {p["raw_name"]: p for p in got["products"]}
    options = lambda name: sorted((o["units"], o["price"]) for o in by_name[name]["retailers"]["bws"]["options"])
    assert options("Test Lager Cans 375ml") == [(1, 5.0), (10, 25.0)]       # no more "10 for $5"
    assert options("Other Lager Cans 375ml") == [(1, 6.0)]                   # still shown, just older


def test_a_pack_that_disappears_stops_being_listed(tmp_path):
    from common.records import Listing, PackType, PriceObservation, Retailer, ScrapedProduct
    conn = db.connect(str(tmp_path / "gone.sqlite3"))
    listing = Listing(retailer=Retailer.BWS, retailer_sku="1", url="https://bws.com.au/p/1", name="Test Lager Cans 375ml", abv=4.5)
    loc = ingest.bws.location_from_set_pickup(json.loads((F / "bws_2606_set_pickup.json").read_text()))
    db.upsert_location(conn, loc)
    day1 = datetime(2026, 10, 1, 4, tzinfo=timezone.utc)
    day2 = day1 + timedelta(days=1)
    mk = lambda pack, units, price, when: PriceObservation(pack_type=pack, units=units, price=price, location_key=loc.location_key, observed_at=when)
    db.save_products(conn, [ScrapedProduct(listing=listing, prices=[mk(PackType.SINGLE, 1, 5.0, day1), mk(PackType.PACK, 6, 24.0, day1)])], day1)
    db.save_products(conn, [ScrapedProduct(listing=listing, prices=[mk(PackType.SINGLE, 1, 5.0, day2)])], day2)   # 6-pack went out of stock
    ingest.run_matching(conn)
    (p,) = queries.compare(conn, "ACT", limit=5, now=day2)["products"]
    assert [(o["units"], o["price"]) for o in p["retailers"]["bws"]["options"]] == [(1, 5.0)]
