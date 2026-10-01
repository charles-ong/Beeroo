"""Print cross-retailer matching results and the review queue.

    python scripts/match_report.py [--db data/beeroo.sqlite3] [--limit 50]

Copy a pair's SKUs into data/match_overrides.csv to force or forbid a merge:

    action,retailer_a,sku_a,retailer_b,sku_b
    merge,bws,709123,liquorland,8776176
    never_merge,bws,111,liquorland,222
"""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from common import db  # noqa: E402
from common.matching import load_overrides, match_listings  # noqa: E402
from common.pipeline import OVERRIDES_PATH  # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default="data/beeroo.sqlite3")
    ap.add_argument("--limit", type=int, default=50)
    args = ap.parse_args()

    conn = db.connect(args.db)
    listings = [item for _, _, item in db.load_listings(conn)]
    clusters, review = match_listings(
        listings, overrides=load_overrides(OVERRIDES_PATH)
    )
    multi = sum(len(c.retailers) > 1 for c in clusters)
    print(f"{len(listings)} listings -> {len(clusters)} products, "
          f"{multi} on 2+ retailers, {len(review)} to review\n")

    for a, b, conf, why in sorted(review, key=lambda r: -r[2])[: args.limit]:
        print(f"{conf:.2f} {why}")
        for x in (a, b):
            print(f"    {x.retailer.value},{x.retailer_sku}  {x.name}")


if __name__ == "__main__":
    main()
