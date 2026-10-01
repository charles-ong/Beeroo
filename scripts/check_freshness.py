"""Show how fresh each retailer's prices are for every state and territory.

    python scripts/check_freshness.py [--db data/beeroo.sqlite3] [--max-age-days 3]
Exit code 0 always (informational); prints STALE / MISSING lines to act on.
"""
import argparse
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from common import db  # noqa: E402
from common.states import STATES  # noqa: E402

RETAILERS = {"bws": "BWS", "liquorland": "Liquorland", "dan_murphys": "Dan Murphy's"}


def freshness(conn, now=None, max_age_days=3):
    """Returns a list of rows: (city, retailer, status, age_days|None)."""
    now = now or datetime.now(timezone.utc)
    latest = {}
    for row in conn.execute(
        "SELECT l.retailer, loc.state, MAX(o.observed_at) AS latest "
        "FROM price_observations o JOIN listings l ON l.id = o.listing_id "
        "JOIN locations loc ON loc.location_key = o.location_key GROUP BY l.retailer, loc.state"
    ):
        latest[(row["retailer"], row["state"])] = datetime.fromisoformat(row["latest"])

    rows = []
    for code, info in STATES.items():
        label = f"{info['name']} ({code})"
        for key in RETAILERS:
            seen = latest.get((key, code))
            if seen is None:
                rows.append((label, key, "MISSING", None))
                continue
            age = (now - seen).total_seconds() / 86400
            rows.append((label, key, "STALE" if age > max_age_days else "ok", round(age, 1)))
    return rows


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--db", default="data/beeroo.sqlite3")
    ap.add_argument("--max-age-days", type=float, default=3)
    args = ap.parse_args()
    if not Path(args.db).exists():
        sys.exit(f"no such database: {args.db}")
    conn = db.connect(args.db)
    rows = freshness(conn, max_age_days=args.max_age_days)
    print(f"{'State':<36}{'Retailer':<14}{'Status':<9}Age")
    for label, key, status, age in rows:
        print(f"{label:<36}{RETAILERS[key]:<14}{status:<9}{'' if age is None else f'{age} days'}")
    bad = sum(1 for r in rows if r[2] != "ok")
    print(f"\n{len(rows) - bad} of {len(rows)} state/retailer combinations are fresh")


if __name__ == "__main__":
    main()
