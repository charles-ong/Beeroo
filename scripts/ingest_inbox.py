"""Ingest pages waiting in a data-branch inbox (scripts/data_branch.py).

    python scripts/ingest_inbox.py --dir data-branch/inbox --db data/beeroo.sqlite3

Files are ingested oldest first. A page that ingests is deleted from the inbox
folder; one that is rejected is moved to <dir>/rejected/ for you to look at.
Exit code 0 even if some pages were rejected (it prints them); 1 on a crash.
"""
import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import ingest  # noqa: E402
from common import db  # noqa: E402
from common.pipeline import run_matching  # noqa: E402


def ingest_inbox(inbox, db_path):
    inbox = Path(inbox)
    files = sorted(p for p in inbox.rglob("*.json") if "rejected" not in p.parts)
    done, rejected = 0, []
    conn = db.connect(str(db_path))

    try:
        for path in files:
            try:
                body = ingest.IngestIn.model_validate(json.loads(path.read_text()))
                ingest.ingest_trusted(conn, body)
            except (ValueError, ingest.IngestError) as e:
                bad = inbox / "rejected" / path.name
                bad.parent.mkdir(parents=True, exist_ok=True)
                path.rename(bad)
                rejected.append((path.name, str(getattr(e, "message", e))[:200]))
                continue
            path.unlink()
            done += 1
        if done:
            run_matching(conn)
    finally:
        conn.close()

    return done, rejected


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--dir", default="data-branch/inbox")
    ap.add_argument("--db", default="data/beeroo.sqlite3")
    args = ap.parse_args(argv)
    if not Path(args.dir).exists():
        print("inbox is empty")
        return 0
    done, rejected = ingest_inbox(args.dir, args.db)
    print(f"ingested {done} page(s) from the inbox")
    for name, why in rejected:
        print(f"REJECTED {name}: {why}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
