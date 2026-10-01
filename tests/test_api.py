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
    act = client.get("/api/locations", params={"postcode": "2606"}).json()
    assert act["state"] == "ACT"
    assert act["retailers"]["bws"]["store_name"] == "Woden" and not act["retailers"]["bws"]["fallback"]
    assert act["retailers"]["liquorland"]["location_key"] == "liquorland:ll_act"

    wa = client.get("/api/locations", params={"postcode": "6000"}).json()
    assert wa["retailers"]["bws"]["store_name"] == "Murray Street"
    assert wa["retailers"]["liquorland"]["location_key"] == "liquorland:ll_wa"


def test_missing_retailer_and_fallback_notices(client):
    vic = client.get("/api/locations", params={"postcode": "3000"}).json()
    text = " ".join(vic["notices"])
    assert "No Dan Murphy's prices loaded yet." in vic["notices"]
    assert "BWS prices for VIC aren't loaded yet" in text
    assert vic["retailers"]["bws"]["fallback"] is True


@pytest.mark.parametrize("bad", ["abc", "123", "12345", "0000", "99999"])
def test_invalid_postcode_is_400(client, bad):
    assert client.get("/api/compare", params={"postcode": bad}).status_code == 400


def test_compare_prices_differ_by_postcode(client):
    def case_price(pc):
        data = client.get("/api/compare", params={"postcode": pc, "q": "great northern original lager 330"}).json()
        prod = next(p for p in data["products"] if "Original Lager" in p["name"] and "Bottle" in p["name"] or p["retailers"].get("bws"))
        opt = next(o for o in prod["retailers"]["bws"]["options"] if o["label"] == "Case of 24")
        return opt["price"]

    assert case_price("2606") == 54.0
    assert case_price("6000") == 62.0


def test_value_sort_puts_unknown_abv_last_and_is_ascending(client):
    products = client.get("/api/compare", params={"postcode": "2606", "limit": 200}).json()["products"]
    values = [p["min_price_per_standard_drink"] for p in products]
    known = [v for v in values if v is not None]
    assert known == sorted(known)
    first_none = values.index(None) if None in values else len(values)
    assert all(v is None for v in values[first_none:])


def test_cheapest_retailer_is_flagged_only_when_two_compete(client):
    products = client.get("/api/compare", params={"postcode": "2606", "limit": 200}).json()["products"]
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


def test_member_offers_hidden_by_default(client):
    off = client.get("/api/compare", params={"postcode": "2606", "limit": 200}).json()["products"]
    on = client.get("/api/compare", params={"postcode": "2606", "limit": 200, "include_member": True}).json()["products"]
    count = lambda ps: sum(len(e["options"]) for p in ps for e in p["retailers"].values())
    assert not any(o["member_only"] for p in off for e in p["retailers"].values() for o in e["options"])
    assert count(on) > count(off)


def test_pack_filter_and_abv_filter(client):
    data = client.get("/api/compare", params={"postcode": "2606", "pack": "case", "limit": 200}).json()
    assert all(o["pack_type"] == "case" for p in data["products"] for e in p["retailers"].values() for o in e["options"])
    strong = client.get("/api/compare", params={"postcode": "2606", "min_abv": 5, "limit": 200}).json()["products"]
    assert strong and all(p["abv"] >= 5 for p in strong)


def test_abv_source_is_exposed_for_borrowed_abv(conn):
    data = queries.compare(conn, "2606", limit=500)
    borrowed = [p for p in data["products"] if "liquorland" in p["retailers"] and p["abv_source"] == "bws"]
    assert borrowed
    p = borrowed[0]
    assert p["retailers"]["liquorland"]["best_value"]["price_per_standard_drink"] is not None


def test_liquorland_only_unknown_abv_has_no_value(conn):
    data = queries.compare(conn, "2606", limit=500)
    unknown = [p for p in data["products"] if p["abv"] is None]
    assert unknown
    for p in unknown:
        for e in p["retailers"].values():
            assert e["best_value"] is None
            assert all(o["price_per_standard_drink"] is None for o in e["options"])


def test_stale_flag(conn):
    data = queries.compare(conn, "2606", limit=500, now=NOW + timedelta(days=30))
    assert all(e["stale"] for p in data["products"] for e in p["retailers"].values())
    fresh = queries.compare(conn, "2606", limit=500, now=NOW)
    assert not any(e["stale"] for p in fresh["products"] for e in p["retailers"].values())


def test_bad_sort_and_pack_are_400(client):
    assert client.get("/api/compare", params={"postcode": "2606", "sort": "nope"}).status_code == 400
    assert client.get("/api/compare", params={"postcode": "2606", "pack": "nope"}).status_code == 400
    assert client.get("/api/compare", params={"postcode": "2606", "retailer": "x"}).status_code == 400


def test_product_detail_and_404(client):
    pid = client.get("/api/compare", params={"postcode": "2606"}).json()["products"][0]["id"]
    detail = client.get(f"/api/products/{pid}", params={"postcode": "2606"})
    assert detail.status_code == 200
    body = detail.json()
    assert body["history"] and not body["demo"]
    series = next(iter(next(iter(body["history"].values())).values()))
    assert series["n"] == 1 and series["signal"] == "not_enough_history"
    assert client.get("/api/products/999999", params={"postcode": "2606"}).status_code == 404


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
    data = client.get("/api/compare", params={"postcode": "2606"}).json()
    assert data["meta"]["demo"] is True
    pid = data["products"][0]["id"]
    detail = client.get(f"/api/products/{pid}", params={"postcode": "2606"}).json()
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
    data = queries.compare(conn, "2606", limit=500, now=NOW)
    prices = {o["price"] for p in data["products"] for e in p["retailers"].values() for o in e["options"]}
    assert row["price"] + 50 not in prices
    assert not any(e["stale"] for p in data["products"] for e in p["retailers"].values())
