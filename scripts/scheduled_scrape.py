"""One polite, unattended Dan Murphy's run. Designed for launchd/cron on YOUR
OWN machine (cloud/datacenter IPs and headless browsers get blocked).

Each run scrapes ONE location (the least-recently-updated of a rotation of
state capitals), then stores the prices locally or pushes them to a deployed
Beeroo server. On any block it stops, backs off for days, and never retries
the same day. See docs/SCHEDULED_SCRAPE.md.

    python scripts/scheduled_scrape.py                       # local DB
    BEEROO_SERVER=https://beeroo.example BEEROO_ADMIN_TOKEN=... \\
        python scripts/scheduled_scrape.py --push            # push to server

Exit codes: 0 ok / nothing to do / backing off, 1 error, 3 blocked.
"""
import argparse
import asyncio
import fcntl
import json
import logging
import os
import random
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from logging.handlers import RotatingFileHandler
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

DEFAULT_STATE = ROOT / "data" / "scrape_state.json"
DEFAULT_DB = ROOT / "data" / "beeroo.sqlite3"
DEFAULT_LOG = ROOT / "data" / "scrape.log"

# State capitals, one per run. Postcodes only choose the nearest Dan Murphy's
# store via the site's own selector.
ZONES = ["2000", "3000", "4000", "6000", "5000", "7000", "2600", "0800"]
MIN_COMPLETE = 0.8       # share of the site's reported total we must collect
MAX_BACKOFF_DAYS = 14
log = logging.getLogger("beeroo.scrape")


# --------------------------------------------------------------------------
# State
# --------------------------------------------------------------------------


def load_state(path):
    try:
        return json.loads(Path(path).read_text())
    except (OSError, ValueError):
        return {"zones": {}, "blocked": {"consecutive": 0, "backoff_until": None}}


def save_state(path, state):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, indent=2, sort_keys=True))
    tmp.replace(path)


def pick_zone(state, zones=ZONES):
    """Never-scraped zones first, then the least recently successful."""
    def last(z):
        return state["zones"].get(z, {}).get("last_success") or ""
    return sorted(zones, key=lambda z: (last(z), zones.index(z)))[0]


def backing_off(state, now):
    until = state.get("blocked", {}).get("backoff_until")
    return datetime.fromisoformat(until) if until and now < datetime.fromisoformat(until) else None


# --------------------------------------------------------------------------
# Pushing to a server
# --------------------------------------------------------------------------


def http_push(server, token, retries=3, timeout=60):
    url = server.rstrip("/") + "/api/admin/ingest"
    if not url.startswith("https://") and not url.startswith("http://127.0.0.1") and not url.startswith("http://localhost"):
        raise ValueError("refusing to send the admin token over plain http to a remote server")

    def push(body):
        data = json.dumps(body).encode()
        last_error = None
        for attempt in range(retries):
            req = urllib.request.Request(
                url, data=data, method="POST",
                headers={"content-type": "application/json", "x-admin-token": token},
            )
            try:
                with urllib.request.urlopen(req, timeout=timeout) as res:
                    return json.loads(res.read())
            except urllib.error.HTTPError as e:
                if e.code < 500:  # a rejection won't get better by retrying
                    raise RuntimeError(f"server rejected ingest: {e.code} {e.read()[:200]!r}")
                last_error = e
            except (urllib.error.URLError, TimeoutError) as e:
                last_error = e
            time.sleep(2 ** attempt)
        raise RuntimeError(f"could not reach server: {last_error}")

    return push


def local_push(db_path):
    from app import contrib
    from common import db

    def push(body):
        conn = db.connect(str(db_path))
        try:
            return contrib.ingest_trusted(conn, contrib.IngestIn.model_validate(body))
        finally:
            conn.close()

    return push


def location_dict(loc):
    return {
        "store_id": loc.store_id, "store_name": loc.store_name,
        "suburb": loc.suburb, "state": loc.state, "postcode": loc.postcode,
    }


# --------------------------------------------------------------------------
# The run
# --------------------------------------------------------------------------


def run(*, state_path, scrape, push, now=None, zone=None, max_pages=40, zones=ZONES,
        sleep=time.sleep, jitter=0):
    """Returns (exit_code, summary dict). `scrape(postcode, max_pages)` is an
    async callable returning dict(location, raw_pages, products, errors, total)
    and raising Blocked when bot protection appears."""
    from scrapers.dan_murphys import Blocked

    now = now or datetime.now(timezone.utc)
    state = load_state(state_path)

    until = backing_off(state, now)
    if until:
        log.info("backing off after earlier blocks until %s; doing nothing", until.isoformat())
        return 0, {"status": "backing_off", "until": until.isoformat()}

    postcode = zone or pick_zone(state, zones)
    entry = state["zones"].setdefault(postcode, {})
    entry["last_attempt"] = now.isoformat()

    if jitter:
        sleep(random.uniform(0, jitter))

    try:
        result = asyncio.run(scrape(postcode, max_pages))
    except Blocked as e:
        n = state.setdefault("blocked", {}).get("consecutive", 0) + 1
        days = min(2 ** n, MAX_BACKOFF_DAYS)
        state["blocked"] = {"consecutive": n, "backoff_until": (now + timedelta(days=days)).isoformat()}
        entry["last_error"] = f"blocked: {e}"
        save_state(state_path, state)
        log.error("BLOCKED (%s). Backing off %d day(s). Not retrying.", e, days)
        return 3, {"status": "blocked", "backoff_days": days, "zone": postcode}
    except Exception as e:  # noqa: BLE001 - unattended job: record, don't crash loudly
        entry["last_error"] = f"{type(e).__name__}: {e}"
        save_state(state_path, state)
        log.exception("scrape failed for %s", postcode)
        return 1, {"status": "error", "zone": postcode, "error": entry["last_error"]}

    location, raw_pages = result["location"], result["raw_pages"]
    total, got = result.get("total"), len(result["products"])
    complete = bool(total) and got >= MIN_COMPLETE * total

    pushed = {"products": 0, "new_observations": 0, "new_listings": 0}
    try:
        for page in raw_pages:
            r = push({
                "kind": "dan_murphys_browse",
                "location": location_dict(location),
                "payload": page,
                "observed_at": now.isoformat(),
            })
            for k in pushed:
                pushed[k] += r.get(k, 0)
    except Exception as e:  # noqa: BLE001
        entry["last_error"] = f"push failed: {e}"
        save_state(state_path, state)
        log.exception("push failed")
        return 1, {"status": "push_failed", "zone": postcode, "error": str(e)}

    state["blocked"] = {"consecutive": 0, "backoff_until": None}
    entry.update(
        last_error=None, store=location.location_key, products=got, expected=total,
        complete=complete, **({"last_success": now.isoformat()} if complete else {}),
    )
    save_state(state_path, state)
    summary = {"status": "ok" if complete else "partial", "zone": postcode,
               "store": location.location_key, "collected": got, "expected": total, **pushed}
    (log.info if complete else log.warning)("run finished: %s", summary)
    return 0, summary


def real_scrape(browser_path=None):
    """The live scraper: a normal, headed Chromium window (headless is blocked)."""
    async def scrape(postcode, max_pages):
        from playwright.async_api import async_playwright
        from scrapers import dan_murphys

        raw = []
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=False, executable_path=browser_path)
            try:
                context = await browser.new_context(
                    viewport={"width": 1440, "height": 900}, locale="en-AU", timezone_id="Australia/Sydney",
                )
                page = await context.new_page()
                location, products, errors, total = await dan_murphys.scrape(page, postcode, max_pages, raw_pages=raw)
            finally:
                await browser.close()
        return {"location": location, "raw_pages": raw, "products": products, "errors": errors, "total": total}

    return scrape


def setup_logging(path):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fmt = logging.Formatter("%(asctime)s %(levelname)s %(message)s")
    for handler in (RotatingFileHandler(path, maxBytes=500_000, backupCount=3), logging.StreamHandler()):
        handler.setFormatter(fmt)
        log.addHandler(handler)
    log.setLevel(logging.INFO)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--push", action="store_true", help="push to $BEEROO_SERVER using $BEEROO_ADMIN_TOKEN (default: local DB)")
    ap.add_argument("--db", default=str(DEFAULT_DB))
    ap.add_argument("--state", default=str(DEFAULT_STATE))
    ap.add_argument("--zone", help="force one postcode (testing)")
    ap.add_argument("--max-pages", type=int, default=40)
    ap.add_argument("--jitter", type=int, default=0, help="random start delay up to N seconds")
    ap.add_argument("--browser-path", default=os.environ.get("BEEROO_CHROMIUM_PATH"))
    args = ap.parse_args(argv)

    setup_logging(DEFAULT_LOG)

    lock = open(ROOT / "data" / ".scrape.lock", "w") if (ROOT / "data").exists() else None
    if lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            log.warning("another scrape is already running; exiting")
            return 0

    if args.push:
        server, token = os.environ.get("BEEROO_SERVER"), os.environ.get("BEEROO_ADMIN_TOKEN")
        if not server or not token:
            log.error("--push needs BEEROO_SERVER and BEEROO_ADMIN_TOKEN in the environment")
            return 1
        push = http_push(server, token)
    else:
        push = local_push(args.db)

    code, summary = run(
        state_path=args.state, scrape=real_scrape(args.browser_path), push=push,
        zone=args.zone, max_pages=args.max_pages, jitter=args.jitter,
    )
    print(json.dumps(summary))
    return code


if __name__ == "__main__":
    sys.exit(main())
