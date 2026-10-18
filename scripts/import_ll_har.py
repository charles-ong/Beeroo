"""Import Liquorland prices from pages YOU browsed in your own browser. No automated visit is involved.

Liquorland's bot protection now rejects our automation, so you collect the data by hand and this reads it:

    python scripts/import_ll_har.py ~/Downloads/liquorland-nsw.har
    python scripts/import_ll_har.py ~/Downloads/*.har --dry-run

Input is a HAR file saved from your browser's Network tab ("Save all as HAR with content") after you set the
state's store and paged through the whole beer list, or the individual JSON responses saved from it. The state of
each page is read from the page itself (the site's own `sitestate`), so there is nothing to tell this script and a
file can't be filed under the wrong state. Steps: docs/LIQUORLAND_MANUAL.md.

Where it goes: if BEEROO_DATA_REMOTE is set (in ~/.config/beeroo/env) the pages go to the cloud job's inbox and the
next daily cloud run adds them to the site; otherwise (or with --local) straight into data/beeroo.sqlite3.
"""
import argparse
import base64
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

from scrapers import liquorland  # noqa: E402
from scrapers.liquorland_live import _LIST_PATH, page_number  # noqa: E402
from import_dm_page import ImportProblem, load_env_file  # noqa: E402

MIN_COMPLETE = 0.8                     # share of the site's reported total we must have (same rule as the scraper)
OUTBOX = ROOT / "data" / "outbox" / "liquorland"
DEFAULT_DB = ROOT / "data" / "beeroo.sqlite3"


def _when(text, fallback):
    try:
        return datetime.fromisoformat(str(text).replace("Z", "+00:00"))
    except ValueError:
        return fallback


def read_pages(path):
    """[(payload, page number or None, observed_at)] for every Liquorland beer-list response in the file."""
    path = Path(path)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        raise ImportProblem(f"cannot read {path} as JSON: {e}")
    saved = datetime.fromtimestamp(path.stat().st_mtime, timezone.utc)

    if isinstance(data, dict) and isinstance(data.get("log"), dict):               # a HAR file
        pages = []
        for entry in data["log"].get("entries") or []:
            url = urlsplit((entry.get("request") or {}).get("url") or "")
            response = entry.get("response") or {}
            content = response.get("content") or {}
            if not _LIST_PATH.match(url.path) or response.get("status") != 200 or not content.get("text"):
                continue
            text = content["text"]
            try:
                if content.get("encoding") == "base64":
                    text = base64.b64decode(text).decode("utf-8")
                payload = json.loads(text)
            except (ValueError, UnicodeDecodeError):
                continue
            if isinstance(payload, dict) and "products" in payload:
                pages.append((payload, page_number(url.query, payload, None),
                              _when(entry.get("startedDateTime"), saved)))
        if not pages:
            raise ImportProblem(f"{path.name} has no Liquorland beer list in it. Did you save the HAR after paging "
                                "through the beer list, with 'Save all as HAR with content'? See docs/LIQUORLAND_MANUAL.md.")
        return pages

    items = data if isinstance(data, list) else [data]                              # saved JSON response(s)
    pages = [(p, None, saved) for p in items if isinstance(p, dict) and "products" in p]
    if not pages:
        raise ImportProblem(f"{path.name} isn't a HAR file or a Liquorland product list")
    return pages


def group_by_state(pages):
    """{site: {"pages": [payload...], "observed_at": datetime}} with repeated pages collapsed (latest wins)."""
    sites = {}
    for index, (payload, number, observed) in enumerate(pages):
        site = liquorland.site_state(payload)
        if not site:
            continue
        slot = sites.setdefault(site, {"by_page": {}, "observed_at": observed})
        key = number if number is not None else ((payload.get("meta") or {}).get("page") or {}).get("current") or f"f{index}"
        slot["by_page"][key] = payload
        slot["observed_at"] = max(slot["observed_at"], observed)
    return {site: {"pages": [s["by_page"][k] for k in sorted(s["by_page"], key=str)], "observed_at": s["observed_at"]}
            for site, s in sites.items()}


def check_state(site, pages, force=False):
    """(entries found, entries the site reports). Refuses a partial list."""
    ids = {p.get("id") for pg in pages for p in pg.get("products") or [] if p.get("id")}
    expected = max(((pg.get("meta") or {}).get("page") or {}).get("productCount") or 0 for pg in pages) or len(ids)
    state = site.split("_", 1)[-1].upper()
    if not ids:
        raise ImportProblem(f"{state}: no products in the pages")
    if len(ids) < MIN_COMPLETE * expected and not force:
        raise ImportProblem(f"{state}: only {len(ids)} of {expected} products. Keep paging (Show 80 results per page, then "
                            "the next-page button) until the last page, then save the HAR again, or pass --force.")
    return len(ids), expected


def build_bodies(site, pages, observed_at):
    return [{"kind": "liquorland_products", "location": None, "observed_at": observed_at.isoformat(),
             "payload": liquorland.minimal_payload(pg)} for pg in pages]


def send(bodies, local=False, db_path=DEFAULT_DB, outbox=OUTBOX, environ=None):
    """Returns a one-line description of where the pages went."""
    import os
    import data_branch
    import scheduled_scrape

    environ = os.environ if environ is None else environ
    remote = environ.get("BEEROO_DATA_REMOTE", "").strip()
    if local or not remote:
        push, total = scheduled_scrape.local_push(db_path), {"products": 0, "new_observations": 0}
        for body in bodies:
            r = push(body)
            total = {k: total[k] + r.get(k, 0) for k in total}
        return f"saved to {db_path}: {total['products']} products, {total['new_observations']} new price observations"

    push = scheduled_scrape.emit_push(outbox)
    for body in bodies:
        push(body)
    try:
        data_branch.push_inbox(remote, outbox, "liquorland")
    except data_branch.DataBranchError as e:
        return f"saved to {outbox} but could not reach the cloud inbox ({e}); run this again to retry"
    return "sent to the cloud inbox; the next daily cloud run will add it to the database and the site"


def main(argv=None, environ=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("files", nargs="+", help="HAR file(s) saved from your browser, or saved JSON responses")
    ap.add_argument("--local", action="store_true", help="write to data/beeroo.sqlite3 even if a cloud inbox is set up")
    ap.add_argument("--dry-run", action="store_true", help="show what was found; send nothing")
    ap.add_argument("--force", action="store_true", help="import even if the list looks incomplete")
    ap.add_argument("--db", default=str(DEFAULT_DB))
    args = ap.parse_args(argv)

    try:
        load_env_file(environ=environ) if environ is not None else load_env_file()
        pages = [p for f in args.files for p in read_pages(f)]
        states = group_by_state(pages)
        if not states:
            raise ImportProblem("none of the pages say which state they are for (no sitestate), so nothing can be filed")
        bodies = []
        for site in sorted(states):
            found, expected = check_state(site, states[site]["pages"], args.force)
            observed = states[site]["observed_at"]
            print(f"{site.split('_', 1)[-1].upper()}: {found} of {expected} products in {len(states[site]['pages'])} pages, "
                  f"saved {observed:%Y-%m-%d %H:%M} UTC")
            bodies += build_bodies(site, states[site]["pages"], observed)
        if args.dry_run:
            print("dry run: nothing sent")
            return 0
        print(send(bodies, local=args.local, db_path=args.db, environ=environ))
    except (ImportProblem, OSError, ValueError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
