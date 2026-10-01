"""Push saved captures (BWS / Liquorland / Dan Murphy's) to a deployed server
through the trusted ingest endpoint. Uses each file's modification time as the
capture time.

    export BEEROO_SERVER=https://your-app.fly.dev BEEROO_ADMIN_TOKEN=...
    python scripts/push_captures.py bws --set-pickup pickup.json a.json b.json
    python scripts/push_captures.py liquorland page1.json page2.json
    python scripts/push_captures.py dan_murphys --store-id 1546 --store-name Thornleigh \\
        --state NSW --postcode 2120 browse.json
"""
import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import scheduled_scrape  # noqa: E402  (reuses the safe HTTP pusher)
from scrapers import bws  # noqa: E402

KINDS = {"bws": "bws_products", "liquorland": "liquorland_products", "dan_murphys": "dan_murphys_browse"}


def build_bodies(retailer, files, location):
    bodies = []
    for path in files:
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
        when = datetime.fromtimestamp(Path(path).stat().st_mtime, timezone.utc)
        bodies.append({"kind": KINDS[retailer], "location": location, "payload": payload,
                       "observed_at": when.isoformat()})
    return bodies


def location_for(args):
    if args.retailer == "bws":
        if not args.set_pickup:
            raise SystemExit("bws needs --set-pickup (the store-selection response)")
        loc = bws.location_from_set_pickup(json.loads(Path(args.set_pickup).read_text()))
        if loc is None:
            raise SystemExit("could not read a store from --set-pickup")
        return scheduled_scrape.location_dict(loc)
    if args.retailer == "dan_murphys":
        if not (args.store_id and args.state and args.postcode):
            raise SystemExit("dan_murphys needs --store-id, --state and --postcode")
        return {"store_id": args.store_id, "store_name": args.store_name, "suburb": None,
                "state": args.state, "postcode": args.postcode}
    return None  # liquorland: the location is inside the payload


def main(argv=None, push=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("retailer", choices=sorted(KINDS))
    ap.add_argument("files", nargs="+")
    ap.add_argument("--set-pickup")
    ap.add_argument("--store-id")
    ap.add_argument("--store-name")
    ap.add_argument("--state")
    ap.add_argument("--postcode")
    args = ap.parse_args(argv)

    if push is None:
        server, token = os.environ.get("BEEROO_SERVER"), os.environ.get("BEEROO_ADMIN_TOKEN")
        if not server or not token:
            raise SystemExit("set BEEROO_SERVER and BEEROO_ADMIN_TOKEN")
        push = scheduled_scrape.http_push(server, token)

    totals = {"products": 0, "new_observations": 0, "new_listings": 0}
    for body in build_bodies(args.retailer, args.files, location_for(args)):
        result = push(body)
        for k in totals:
            totals[k] += result.get(k, 0)
    print(totals)
    return 0


if __name__ == "__main__":
    sys.exit(main())
