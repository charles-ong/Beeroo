"""Build data/demo.sqlite3 for development and demos.

Current prices are REAL (from the captured fixtures). Price HISTORY is
SIMULATED (seeded random walk ending at the real current price) because
real history doesn't exist yet. The DB is flagged demo=1 so the UI shows a
visible "demo data" banner. Never point production at this file.

    python scripts/build_demo_db.py            # curated, committed fixtures
    python scripts/build_demo_db.py --raw      # richer: your local raw capture folders
"""
import argparse
import glob
import json
import random
import re
import sys
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

import ingest  # noqa: E402

from common import db  # noqa: E402
from common.records import Location, Retailer  # noqa: E402

F = ROOT / "tests" / "fixtures"
WEEKS = 8


def _idx(path):
    return int(re.match(r"(\d+)_", Path(path).name).group(1))


def load_real(conn, raw):
    def pickup(name):
        return ingest.bws.location_from_set_pickup(json.loads((F / name).read_text()))

    if raw and (F / "bws").exists():
        for folder, pick in (("bws", "bws_2606_set_pickup.json"), ("bws6000", "bws_6000_set_pickup.json")):
            marker = _idx(glob.glob(str(F / folder / "*SetPickupByStoreNo*"))[0])
            files = [f for f in sorted(glob.glob(str(F / folder / "*ProductGroup_Products*.json")))
                     if _idx(f) > marker and json.loads(Path(f).read_text()).get("Items")]
            ingest.ingest_files(conn, "bws", files, pickup(pick))
        ll = sorted(glob.glob(str(F / "liquorland*" / "*products_ll_*beer_and_cider.json")))
        ll = [f for f in ll if json.loads(Path(f).read_text()).get("products")]
        ingest.ingest_files(conn, "liquorland", ll, None)
    else:
        for name, pick in (("bws_2606", "bws_2606_set_pickup.json"), ("bws_6000", "bws_6000_set_pickup.json")):
            ingest.ingest_files(conn, "bws", [F / f"{name}_products.json"], pickup(pick))
        ingest.ingest_files(conn, "liquorland", [F / "liquorland_act_products.json", F / "liquorland_wa_products.json"], None)

    dm = Location(retailer=Retailer.DAN_MURPHYS, store_id="1546", store_name="Thornleigh", state="NSW", postcode="2120")
    ingest.ingest_files(conn, "dan_murphys", [F / "dan_murphys_browse_page1.json"], dm)
    ingest.run_matching(conn)


def simulate_history(conn, seed=7):
    rng = random.Random(seed)
    rows = conn.execute(
        "SELECT listing_id, location_key, pack_type, units, member_only, price, observed_at "
        "FROM price_observations"
    ).fetchall()
    inserted = 0

    for row in rows:
        now = datetime.fromisoformat(row["observed_at"])
        price = row["price"]
        step = max(round(price * 0.02, 1), 0.5)

        for week in range(1, WEEKS + 1):
            if rng.random() < 0.35:  # occasionally a promo/price move
                price = max(0.5, round((price + rng.choice([-2, -1, 1, 2]) * step) * 2) / 2)
            conn.execute(
                "INSERT INTO price_observations (listing_id, location_key, pack_type, units, "
                "member_only, price, observed_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (row["listing_id"], row["location_key"], row["pack_type"], row["units"],
                 row["member_only"], price, (now - timedelta(days=7 * week)).isoformat()),
            )
            inserted += 1

    conn.commit()
    return inserted


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--raw", action="store_true")
    ap.add_argument("--out", default=str(ROOT / "data" / "demo.sqlite3"))
    args = ap.parse_args()

    out = Path(args.out)
    out.parent.mkdir(exist_ok=True)
    out.unlink(missing_ok=True)

    conn = db.connect(str(out))
    load_real(conn, args.raw)
    n = simulate_history(conn)
    db.set_meta(conn, "demo", "1")
    print(f"{conn.execute('SELECT COUNT(*) FROM products').fetchone()[0]} products; "
          f"{n} SIMULATED history rows added; demo flag set -> {out}")


if __name__ == "__main__":
    main()
