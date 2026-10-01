import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))

import ingest  # noqa: E402

from common import db  # noqa: E402
from common.matching import compare, consensus_abv, match_listings, signature  # noqa: E402
from common.records import Listing, Retailer  # noqa: E402
from common.value import value_metrics  # noqa: E402

F = Path(__file__).parent / "fixtures"
DM, BWS, LL = Retailer.DAN_MURPHYS, Retailer.BWS, Retailer.LIQUORLAND


def L(retailer, sku, name, abv=None, vol=None):
    return Listing(retailer=retailer, retailer_sku=sku, url=f"https://x/{sku}", name=name, abv=abv, unit_volume_ml=vol)


def test_same_product_different_wording_matches():
    a = L(DM, "1", "Carlton Dry Lager Bottles 330mL")
    b = L(BWS, "2", "Carlton Dry Lager Bottles 330ml")
    c = L(LL, "3", "Carlton Dry Bottle 330mL")
    clusters, review = match_listings([a, b, c])
    assert len(clusters) == 1 and clusters[0].retailers == {DM, BWS, LL}
    assert review == []


@pytest.mark.parametrize(
    "a,b,why",
    [
        ("Carlton Dry Bottle 330mL", "Carlton Dry Bottle 375mL", "volume"),
        ("Hahn Super Dry Can 375mL", "Hahn Super Dry Bottle 375mL", "packaging"),
        ("Pure Blonde Ultra Low Carb Bottle 330mL", "Brite Blonde Ultra Low Carb Bottle 330mL", "name"),
        ("Hahn Super Dry Bottle 330mL", "Hahn Super Dry 3.5 Bottle 330mL", "extra number token"),
        ("Asahi Super Dry Bottle 330mL", "Asahi Super Dry Black Bottle 330mL", "extra word"),
    ],
)
def test_different_products_are_not_merged(a, b, why):
    clusters, _ = match_listings([L(DM, "1", a), L(LL, "2", b)])
    assert len(clusters) == 2, why


def test_abv_conflict_blocks_match():
    sa = signature(L(DM, "1", "Foo Bar Bottle 330mL", abv=4.0))
    sb = signature(L(LL, "2", "Foo Bar Bottle 330mL", abv=3.5))
    assert compare(sa, sb) == (0.0, "abv conflict")


def test_near_miss_goes_to_review_not_merged():
    a = L(DM, "1", "James Squire Ginger Beer Cans 330mL")
    b = L(BWS, "2", "James Squire Lower Sugar Ginger Beer Cans 330ml")
    clusters, review = match_listings([a, b])
    assert len(clusters) == 2
    assert len(review) == 1 and review[0][2] < 1.0


def test_cluster_never_holds_two_listings_from_one_retailer():
    a = L(DM, "1", "Carlton Dry Bottle 330mL")
    b = L(DM, "2", "Carlton Dry Bottles 330mL")
    clusters, _ = match_listings([a, b])
    assert len(clusters) == 2


def test_abv_is_borrowed_from_matched_retailer():
    dm = L(DM, "1", "Carlton Dry Lager Bottles 330mL")            # DM name has no ABV
    bws = L(BWS, "2", "Carlton Dry Lager Bottles 330ml", abv=4.5)
    ll = L(LL, "3", "Carlton Dry Bottle 330mL")
    clusters, _ = match_listings([dm, bws, ll])
    assert consensus_abv(clusters[0]) == (4.5, BWS)


def test_value_metrics_known_abv():
    m = value_metrics(54.95, 30, 375, 3.5, "dan_murphys")
    assert m["status"] == "ok"
    assert m["price_per_standard_drink"] == pytest.approx(1.769, abs=0.001)
    assert m["standard_drinks"] == pytest.approx(31.07, abs=0.01)
    assert m["abv_source"] == "dan_murphys"


def test_value_metrics_unknown_abv_invents_nothing():
    m = value_metrics(51, 24, 330, None)
    assert m["status"] == "abv_unknown"
    assert m["price_per_standard_drink"] is None and m["standard_drinks"] is None


def test_value_metrics_zero_alcohol():
    assert value_metrics(15, 6, 330, 0.0)["status"] == "zero_alcohol"


def test_ingest_and_match_end_to_end(tmp_path):
    conn = db.connect()
    when = "2026-10-01T04:00:00+00:00"
    pickup = ingest.bws.location_from_set_pickup(json.loads((F / "bws_2606_set_pickup.json").read_text()))

    n, inserted, errors = ingest.ingest_files(conn, "bws", [F / "bws_2606_products.json"], pickup, when)
    assert n == 14 and inserted > 40 and len(errors) == 2

    n2, inserted2, _ = ingest.ingest_files(conn, "liquorland", [F / "liquorland_act_products.json"], None, when)
    assert n2 > 20

    # re-ingesting the same capture adds nothing (same price, same day)
    _, again, _ = ingest.ingest_files(conn, "bws", [F / "bws_2606_products.json"], pickup, when)
    assert again == 0

    saved, multi, _ = ingest.run_matching(conn)
    assert saved == n + n2 - multi
    assert multi >= 1  # fixtures overlap on Carlton Dry / Great Northern etc.

    # matching is idempotent
    saved_again, _, _ = ingest.run_matching(conn)
    assert conn.execute("SELECT COUNT(*) c FROM products").fetchone()["c"] == saved_again == saved

    # a Liquorland listing (no ABV of its own) gets ABV from its BWS match
    row = conn.execute(
        """
        SELECT p.abv, p.abv_source, l.abv AS own_abv FROM listings l
        JOIN products p ON p.id = l.product_id
        WHERE l.retailer = 'liquorland' AND l.abv IS NULL AND p.abv IS NOT NULL LIMIT 1
        """
    ).fetchone()
    assert row is not None and row["abv_source"] == "bws" and row["own_abv"] is None

    # observation time is the capture time, not now
    ts = conn.execute("SELECT MIN(observed_at) m FROM price_observations").fetchone()["m"]
    assert ts.startswith("2026-10-01T04:00:00")
