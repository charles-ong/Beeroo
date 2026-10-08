"""One-off maintenance on a Beeroo database.

    python scripts/maintain_db.py purge-locations 'dan_murphys:manual_%' --db data/beeroo.sqlite3 [--dry-run]
    python scripts/maintain_db.py merge other.sqlite3 --db data/beeroo.sqlite3

purge-locations  delete everything recorded at the matching location keys (SQL LIKE): their prices,
                 the locations, listings left with no prices at all, and products left with no listing.
merge            copy another database's locations, listings and price history into this one
                 (e.g. a CI artifact from a verified scrape). Same-day, same-price rows are skipped.

For the live database, restore it from the data branch, run this, then save it back:
    python scripts/data_branch.py restore --clone data-branch --into data
    python scripts/maintain_db.py ... --db data/beeroo.sqlite3
    python scripts/data_branch.py save --clone data-branch --db data/beeroo.sqlite3
"""
import argparse
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from common import db  # noqa: E402
from common.pipeline import run_matching  # noqa: E402
from common.records import Location, PackType, PriceObservation, Retailer  # noqa: E402


def purge_locations(conn, like, dry_run=False):
    """Returns counts of what was (or would be) removed."""
    keys = [r["location_key"] for r in conn.execute("SELECT location_key FROM locations WHERE location_key LIKE ?", (like,))]
    counts = {"locations": len(keys), "observations": 0, "listings": 0, "products": 0}
    if not keys:
        return counts
    marks = ",".join("?" * len(keys))
    counts["observations"] = conn.execute(f"SELECT COUNT(*) FROM price_observations WHERE location_key IN ({marks})", keys).fetchone()[0]

    if dry_run:
        return counts

    conn.execute(f"DELETE FROM price_observations WHERE location_key IN ({marks})", keys)
    conn.execute(f"DELETE FROM locations WHERE location_key IN ({marks})", keys)
    counts["listings"] = conn.execute(
        "DELETE FROM listings WHERE id NOT IN (SELECT DISTINCT listing_id FROM price_observations)").rowcount
    counts["products"] = conn.execute(
        "DELETE FROM products WHERE id NOT IN (SELECT DISTINCT product_id FROM listings WHERE product_id IS NOT NULL)").rowcount
    conn.commit()
    run_matching(conn)
    return counts


def merge(conn, source_path):
    """Copy locations, listings and observations from another database."""
    src = sqlite3.connect(f"file:{source_path}?mode=ro", uri=True)
    src.row_factory = sqlite3.Row
    counts = {"locations": 0, "listings": 0, "observations": 0}
    try:
        for row in src.execute("SELECT * FROM locations"):
            db.upsert_location(conn, Location(
                retailer=Retailer(row["retailer"]), store_id=row["store_id"], store_name=row["store_name"],
                suburb=row["suburb"], state=row["state"], postcode=row["postcode"]))
            counts["locations"] += 1

        ids = {}
        for sid, _, listing in db.load_listings(src):
            when = src.execute("SELECT last_seen FROM listings WHERE id = ?", (sid,)).fetchone()["last_seen"]
            ids[sid] = db.upsert_listing(conn, listing, datetime.fromisoformat(when))
            counts["listings"] += 1

        for row in src.execute("SELECT * FROM price_observations ORDER BY observed_at, id"):
            obs = PriceObservation(pack_type=PackType(row["pack_type"]), units=row["units"], price=row["price"],
                                   member_only=bool(row["member_only"]), location_key=row["location_key"],
                                   observed_at=datetime.fromisoformat(row["observed_at"]))
            counts["observations"] += bool(db.record_observation(conn, ids[row["listing_id"]], obs))
        conn.commit()
    finally:
        src.close()
    run_matching(conn)
    return counts


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--db", default="data/beeroo.sqlite3")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("purge-locations")
    p.add_argument("like")
    p.add_argument("--dry-run", action="store_true")
    m = sub.add_parser("merge")
    m.add_argument("source")
    args = ap.parse_args(argv)

    if not Path(args.db).exists():
        sys.exit(f"no such database: {args.db}")
    conn = db.connect(args.db)
    if args.cmd == "purge-locations":
        counts = purge_locations(conn, args.like, args.dry_run)
        print(("would remove " if args.dry_run else "removed ") + ", ".join(f"{n} {k}" for k, n in counts.items()))
    else:
        counts = merge(conn, args.source)
        print("merged " + ", ".join(f"{n} {k}" for k, n in counts.items()))


if __name__ == "__main__":
    main()
