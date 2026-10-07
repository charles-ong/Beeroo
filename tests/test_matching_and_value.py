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


# --- descriptor-subset matching and manual overrides -------------------------

from common.matching import Overrides, load_overrides  # noqa: E402


@pytest.mark.parametrize(
    "a,b",
    [
        ("Sapporo Premium Lager Bottles 355ml", "Sapporo Bottles 355mL"),
        ("Coopers Original Pale Ale Cans 375ml", "Coopers Pale Ale Can 375mL"),
        ("Stone's Ginger Joe Alcoholic Beer Bottles 500ml", "Stones Ginger Joe Bottle 500mL"),
        ("Sol Cerveza Original Bottles 330ml", "Sol Beer Bottle 330mL"),
        ("Brookvale Union Ginger Beer Cans 10 Pack 330ml", "Brookvale Union Ginger Beer Can 330mL 10pk"),
    ],
)
def test_descriptor_only_differences_merge(a, b):
    clusters, review = match_listings([L(BWS, "1", a), L(LL, "2", b)])
    assert len(clusters) == 1 and clusters[0].retailers == {BWS, LL}
    assert review == []


@pytest.mark.parametrize(
    "bws_name,ll_names",
    [
        # three different Liquorland beers share the BWS name as a prefix
        ("Mountain Culture Juice Trip Cans 355ml",
         ["Mountain Culture Juice Trip Neon Splice Can 355mL",
          "Mountain Culture Juice Trip Fruit Hazy Can 355mL"]),
        # Block Can vs Can: two candidates, so ambiguous
        ("Xxxx Gold Mid Strength Lager Beer Cans 375ml",
         ["XXXX Gold Block Can 375mL", "XXXX Gold Can 375mL"]),
        # "mid" is a strength variant, not a harmless descriptor
        ("Victoria Bitter Mid Cans 375ml", ["Victoria Bitter Can 375mL"]),
    ],
)
def test_ambiguous_or_variant_subsets_go_to_review(bws_name, ll_names):
    listings = [L(BWS, "b", bws_name)] + [
        L(LL, f"l{i}", n) for i, n in enumerate(ll_names)
    ]
    clusters, review = match_listings(listings)
    assert all(len(c.retailers) == 1 for c in clusters)
    assert review


def test_descriptor_match_needs_both_sides_unique():
    # one Liquorland listing is the only candidate for two BWS listings
    listings = [
        L(BWS, "1", "Sapporo Premium Lager Bottles 355ml"),
        L(BWS, "2", "Sapporo Original Bottles 355ml"),
        L(LL, "3", "Sapporo Bottles 355mL"),
    ]
    clusters, review = match_listings(listings)
    assert all(len(c.retailers) == 1 for c in clusters)
    assert review


def test_descriptor_match_respects_abv_conflict():
    a = L(BWS, "1", "Foo Premium Bottle 330ml", abv=4.0)
    b = L(LL, "2", "Foo Bottle 330mL", abv=5.0)
    clusters, _ = match_listings([a, b])
    assert len(clusters) == 2


def test_override_forces_merge_and_blocks():
    a = L(BWS, "1", "Mountain Culture Juice Trip Cans 355ml")
    b = L(LL, "2", "Mountain Culture Juice Trip Fruit Hazy Can 355mL")
    forced = Overrides(merge=[((BWS, "1"), (LL, "2"))])
    clusters, _ = match_listings([a, b], overrides=forced)
    assert len(clusters) == 1

    x = L(BWS, "3", "Sapporo Premium Lager Bottles 355ml")
    y = L(LL, "4", "Sapporo Bottles 355mL")
    never = Overrides(never={frozenset(((BWS, "3"), (LL, "4")))})
    clusters, review = match_listings([x, y], overrides=never)
    assert len(clusters) == 2 and review == []


def test_load_overrides_csv(tmp_path):
    p = tmp_path / "o.csv"
    p.write_text(
        "action,retailer_a,sku_a,retailer_b,sku_b\n"
        "merge,bws,1,liquorland,2\n"
        "never_merge,bws,3,liquorland,4\n"
    )
    o = load_overrides(p)
    assert o.merge == [((BWS, "1"), (LL, "2"))]
    assert o.blocked((LL, "4"), (BWS, "3"))
    assert load_overrides(tmp_path / "missing.csv").merge == []

    p.write_text("action,retailer_a,sku_a,retailer_b,sku_b\nmaybe,bws,1,liquorland,2\n")
    with pytest.raises(ValueError):
        load_overrides(p)


def test_zero_and_zero_non_alcoholic_are_the_same_beer():
    bws = L(BWS, "1", "Carlton Zero Zero Non Alcoholic Beer Bottles 330ml", abv=0.0)
    ll = L(LL, "2", "Carlton Zero Bottle 330mL")
    clusters, review = match_listings([bws, ll])
    assert len(clusters) == 1 and clusters[0].retailers == {BWS, LL} and review == []


def test_but_a_non_alcoholic_version_of_a_regular_beer_is_not_the_regular_beer():
    clusters, _ = match_listings([L(BWS, "1", "Carlton Dry Non Alcoholic Beer Bottles 330ml", abv=0.0),
                                  L(LL, "2", "Carlton Dry Bottle 330mL")])
    assert len(clusters) == 2
    clusters, _ = match_listings([L(BWS, "1", "Carlton Zero Zero Non Alcoholic Beer Bottles 330ml", abv=0.0),
                                  L(LL, "2", "Carlton Zero Bottle 330mL", abv=4.0)])
    assert len(clusters) == 2                                      # a known ABV that disagrees still blocks it
