"""Show how fresh each retailer's prices are per location, and which manual
captures are due (Liquorland is refreshed by hand, see docs/LIQUORLAND_MANUAL.md).

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

# The four cities we cover, and the retailer location key each one maps to.
EXPECTED = {
    "Sydney (NSW)": {"state": "NSW", "postcode": "2000"},
    "Canberra (ACT)": {"state": "ACT", "postcode": "2600"},
    "Melbourne (VIC)": {"state": "VIC", "postcode": "3000"},
    "Perth (WA)": {"state": "WA", "postcode": "6000"},
}
RETAILERS = {"bws": "BWS", "dan_murphys": "Dan Murphy's", "liquorland": "Liquorland"}
MANUAL = {"liquorland"}


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
    for city, info in EXPECTED.items():
        for key in RETAILERS:
            seen = latest.get((key, info["state"]))
            if seen is None:
                rows.append((city, key, "MISSING", None))
                continue
            age = (now - seen).total_seconds() / 86400
            rows.append((city, key, "STALE" if age > max_age_days else "ok", round(age, 1)))
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
    print(f"{'City':<18}{'Retailer':<14}{'Status':<9}Age")
    for city, key, status, age in rows:
        how = " (refresh by hand)" if status != "ok" and key in MANUAL else ""
        print(f"{city:<18}{RETAILERS[key]:<14}{status:<9}{'' if age is None else f'{age} days'}{how}")
    due = [f"{RETAILERS[k]} {c}" for c, k, s, _ in rows if s != "ok" and k in MANUAL]
    if due:
        print("\nManual captures due:", "; ".join(due))


if __name__ == "__main__":
    main()
