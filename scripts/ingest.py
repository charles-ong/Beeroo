"""Load captured data into the SQLite DB, then (re)run product matching.

    python scripts/ingest.py bws --products a.json b.json --set-pickup pickup.json
    python scripts/ingest.py liquorland --products a.json b.json
    python scripts/ingest.py dan_murphys --products browse.json \\
        --store-id 1546 --store-name Thornleigh --state NSW --postcode 2120
    python scripts/ingest.py match

Observation time defaults to each file's modification time (when it was
captured), not "now". Override with --observed-at (ISO 8601).
"""
import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from common import db, pipeline  # noqa: E402
from common.records import Location, Retailer  # noqa: E402
from scrapers import bws, endeavour, liquorland  # noqa: E402

DEFAULT_DB = "data/beeroo.sqlite3"


def observed_at_for(path, override):
    if override:
        return datetime.fromisoformat(override)
    return datetime.fromtimestamp(Path(path).stat().st_mtime, timezone.utc)


def ingest_files(conn, retailer, files, location, override=None):
    """Parse each file with the retailer's parser and store it.
    Returns (listings, observations_inserted, errors)."""
    total_products, inserted, errors = 0, 0, []

    for path in files:
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
        when = observed_at_for(path, override)

        if retailer == "bws":
            if location is None:
                raise ValueError("bws needs --set-pickup (store location)")
            products, errs = bws.parse_bws_payload(payload, location.location_key, when)
        elif retailer == "liquorland":
            site = liquorland.site_state(payload)
            if site is None:
                raise ValueError(f"{path}: no sitestate in response")
            location = liquorland.location_for_site(site)
            products, errs = liquorland.parse_liquorland_payload(
                payload, location.location_key, when
            )
        elif retailer == "dan_murphys":
            if location is None:
                raise ValueError("dan_murphys needs --store-id/--state/--postcode")
            products, errs = endeavour.parse_browse_payload(
                payload, location.location_key, when
            )
        else:
            raise ValueError(f"unknown retailer {retailer}")

        db.upsert_location(conn, location)
        inserted += db.save_products(conn, products, when)
        total_products += len(products)
        errors += [(Path(path).name, *e) for e in errs]

    return total_products, inserted, errors


run_matching = pipeline.run_matching


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("retailer", choices=["bws", "liquorland", "dan_murphys", "match"])
    ap.add_argument("--products", nargs="+", default=[])
    ap.add_argument("--set-pickup", help="BWS Address/SetPickupByStoreNo response")
    ap.add_argument("--store-id")
    ap.add_argument("--store-name")
    ap.add_argument("--state")
    ap.add_argument("--postcode")
    ap.add_argument("--observed-at")
    ap.add_argument("--db", default=DEFAULT_DB)
    args = ap.parse_args(argv)

    conn = db.connect(args.db)

    if args.retailer != "match":
        location = None

        if args.set_pickup:
            location = bws.location_from_set_pickup(
                json.loads(Path(args.set_pickup).read_text(encoding="utf-8"))
            )
        elif args.store_id:
            location = Location(
                retailer=Retailer.DAN_MURPHYS,
                store_id=args.store_id,
                store_name=args.store_name,
                state=args.state,
                postcode=args.postcode,
            )

        products, inserted, errors = ingest_files(
            conn, args.retailer, args.products, location, args.observed_at
        )
        print(f"{products} products parsed, {inserted} new observations, {len(errors)} skipped")
        for name, sku, reason in errors[:10]:
            print(f"  {name}: {sku}: {reason}")

    saved, multi, review = run_matching(conn)
    print(f"matching: {saved} canonical products, {multi} matched across retailers, {len(review)} near-misses for review")
    conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
