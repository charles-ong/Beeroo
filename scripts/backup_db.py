"""Consistent online backup of the SQLite DB (safe while the app is running).

    python scripts/backup_db.py [--db data/beeroo.sqlite3] [--keep 14]
"""
import argparse
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path


def backup(db_path, out_dir=None, keep=14, now=None):
    db_path = Path(db_path)
    out_dir = Path(out_dir or db_path.parent / "backups")
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = (now or datetime.now(timezone.utc)).strftime("%Y%m%d-%H%M%S")
    target = out_dir / f"{db_path.stem}-{stamp}.sqlite3"

    src = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    dst = sqlite3.connect(target)
    try:
        src.backup(dst)
        result = dst.execute("PRAGMA integrity_check").fetchone()[0]
    finally:
        dst.close()
        src.close()

    if result != "ok":
        target.unlink(missing_ok=True)
        raise RuntimeError(f"backup failed integrity check: {result}")

    old = sorted(out_dir.glob(f"{db_path.stem}-*.sqlite3"))[:-keep] if keep else []
    for path in old:
        path.unlink()
    return target


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--db", default="data/beeroo.sqlite3")
    ap.add_argument("--out")
    ap.add_argument("--keep", type=int, default=14)
    args = ap.parse_args()
    if not Path(args.db).exists():
        sys.exit(f"no such database: {args.db}")
    print(backup(args.db, args.out, args.keep))


if __name__ == "__main__":
    main()
