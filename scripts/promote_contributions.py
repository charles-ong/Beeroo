"""Promote agreed contributions and expire unconfirmed ones (run daily via cron).

    python scripts/promote_contributions.py [--db data/beeroo.sqlite3]
"""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import contrib  # noqa: E402
from common import db  # noqa: E402


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--db", default="data/beeroo.sqlite3")
    args = ap.parse_args()
    conn = db.connect(args.db)
    stats = contrib.promote(conn)
    stats["expired"] = contrib.expire(conn)
    print(stats, contrib.health(conn))


if __name__ == "__main__":
    main()
