import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))
import maintain_db  # noqa: E402

from common import db  # noqa: E402
from common.pipeline import run_matching  # noqa: E402
from common.records import Listing, Location, PackType, PriceObservation, Retailer, ScrapedProduct  # noqa: E402

DAY1 = datetime(2026, 10, 2, 3, tzinfo=timezone.utc)
DAY2 = datetime(2026, 10, 8, 3, tzinfo=timezone.utc)


def add(conn, retailer, store, state, sku, name, price, when, units=6):
    loc = Location(retailer=retailer, store_id=store, state=state, postcode={"NSW": "2100", "ACT": "2600"}[state])
    db.upsert_location(conn, loc)
    item = Listing(retailer=retailer, retailer_sku=sku, url=f"https://x/{sku}", name=name, rating=4.2, review_count=10)
    obs = PriceObservation(pack_type=PackType.PACK, units=units, price=price, location_key=loc.location_key, observed_at=when)
    db.save_products(conn, [ScrapedProduct(listing=item, prices=[obs])], when)
    return loc.location_key


@pytest.fixture()
def live(tmp_path):
    conn = db.connect(str(tmp_path / "live.sqlite3"))
    DM, BWS = Retailer.DAN_MURPHYS, Retailer.BWS
    add(conn, DM, "manual_nsw", "NSW", "1", "Foo Lager Cans 375ml", 20.0, DAY1)
    add(conn, DM, "manual_act", "ACT", "1", "Foo Lager Cans 375ml", 20.0, DAY1)
    add(conn, DM, "manual_act", "ACT", "2", "Only Imported Pale Ale Cans 375ml", 25.0, DAY1)
    add(conn, BWS, "1", "ACT", "b1", "Foo Lager Cans 375ml", 19.0, DAY1)
    run_matching(conn)
    return conn


def count(conn, table):
    return conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]


def test_purge_removes_only_the_matching_locations_and_what_they_leave_behind(live):
    counts = maintain_db.purge_locations(live, "dan_murphys:manual_%")
    assert counts == {"locations": 2, "observations": 3, "listings": 2, "products": 1}
    assert {r["location_key"] for r in live.execute("SELECT location_key FROM locations")} == {"bws:1"}
    assert [r["name"] for r in live.execute("SELECT name FROM listings")] == ["Foo Lager Cans 375ml"]      # BWS's survives
    assert count(live, "price_observations") == 1 and count(live, "products") == 1


def test_purge_dry_run_changes_nothing(live):
    before = [count(live, t) for t in ("locations", "listings", "price_observations", "products")]
    assert maintain_db.purge_locations(live, "dan_murphys:manual_%", dry_run=True)["observations"] == 3
    assert [count(live, t) for t in ("locations", "listings", "price_observations", "products")] == before


def test_purge_with_no_match_is_a_no_op(live):
    assert maintain_db.purge_locations(live, "nothing:%") == {"locations": 0, "observations": 0, "listings": 0, "products": 0}


def test_a_listing_that_also_has_real_prices_is_kept_when_only_the_manual_ones_go(live):
    add(live, Retailer.DAN_MURPHYS, "1462", "ACT", "1", "Foo Lager Cans 375ml", 21.49, DAY2)
    maintain_db.purge_locations(live, "dan_murphys:manual_%")
    rows = live.execute("SELECT o.location_key, o.price FROM price_observations o JOIN listings l ON l.id = o.listing_id "
                        "WHERE l.retailer = 'dan_murphys'").fetchall()
    assert [tuple(r) for r in rows] == [("dan_murphys:1462", 21.49)]


def test_merge_brings_in_a_verified_scrape_with_its_history_and_ratings(live, tmp_path):
    artifact = db.connect(str(tmp_path / "artifact.sqlite3"))
    add(artifact, Retailer.DAN_MURPHYS, "1462", "ACT", "1", "Foo Lager Cans 375ml", 21.49, DAY2)
    add(artifact, Retailer.DAN_MURPHYS, "1462", "ACT", "3", "New Dark Ale Cans 375ml", 30.0, DAY2)
    artifact.close()
    counts = maintain_db.merge(live, str(tmp_path / "artifact.sqlite3"))
    assert counts == {"locations": 1, "listings": 2, "observations": 2}
    got = live.execute("SELECT name, rating, review_count FROM listings WHERE retailer_sku = '3'").fetchone()
    assert tuple(got) == ("New Dark Ale Cans 375ml", 4.2, 10)
    assert live.execute("SELECT state FROM locations WHERE location_key = 'dan_murphys:1462'").fetchone()[0] == "ACT"
    # "Foo Lager" at DM matched the BWS listing of the same beer
    pid = live.execute("SELECT product_id FROM listings WHERE retailer = 'dan_murphys' AND retailer_sku = '1'").fetchone()[0]
    assert pid == live.execute("SELECT product_id FROM listings WHERE retailer = 'bws'").fetchone()[0]


def test_merging_twice_adds_nothing(live, tmp_path):
    artifact = db.connect(str(tmp_path / "a.sqlite3"))
    add(artifact, Retailer.DAN_MURPHYS, "1462", "ACT", "1", "Foo Lager Cans 375ml", 21.49, DAY2)
    artifact.close()
    maintain_db.merge(live, str(tmp_path / "a.sqlite3"))
    before = count(live, "price_observations")
    assert maintain_db.merge(live, str(tmp_path / "a.sqlite3"))["observations"] == 0
    assert count(live, "price_observations") == before


def test_command_line(tmp_path, capsys):
    path = tmp_path / "x.sqlite3"
    conn = db.connect(str(path))
    add(conn, Retailer.DAN_MURPHYS, "manual_nsw", "NSW", "1", "Foo Lager Cans 375ml", 20.0, DAY1)
    conn.close()
    maintain_db.main(["--db", str(path), "purge-locations", "dan_murphys:manual_%", "--dry-run"])
    assert "would remove 1 locations, 1 observations" in capsys.readouterr().out
    with pytest.raises(SystemExit):
        maintain_db.main(["--db", str(tmp_path / "nope.sqlite3"), "merge", str(path)])
