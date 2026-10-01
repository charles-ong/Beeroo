"""Polite, unattended daily scraping on YOUR OWN machine (cloud/datacenter IPs and
headless browsers get blocked) for BWS, Liquorland and Dan Murphy's.

Every state and territory is priced from "the store nearest its postcode"
(common/states.py, e.g. 2100 for NSW). Each run visits each state once per
retailer in a visible browser window, interleaving the retailers so no site
sees back-to-back sessions, then stores the prices locally or pushes them to a
Beeroo server. If a site shows bot protection or a CAPTCHA we never try to get
past it: that retailer stops, backs off for days, and is not retried the same
day. Retailers back off independently.

Liquorland note: its robots.txt disallows the /api/ path its pages load data
from. It is included at your request; skip it with BEEROO_SKIP_RETAILERS=liquorland.

    python scripts/scheduled_scrape.py                           # all retailers, all states
    python scripts/scheduled_scrape.py --retailer bws --zone NSW  # one state, one site
    BEEROO_SERVER=https://beeroo.example BEEROO_ADMIN_TOKEN=... \\
        python scripts/scheduled_scrape.py --push

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

from common.states import STATES  # noqa: E402

DEFAULT_STATE = ROOT / "data" / "scrape_state.json"
DEFAULT_DB = ROOT / "data" / "beeroo.sqlite3"
DEFAULT_LOG = ROOT / "data" / "scrape.log"

ZONES = list(STATES)                       # state codes; postcode = STATES[code]["postcode"]
RETAILERS = ("bws", "liquorland", "dan_murphys")   # interleave order (the reliable ones first)
KINDS = {"bws": "bws_products", "liquorland": "liquorland_products", "dan_murphys": "dan_murphys_browse"}
MIN_COMPLETE = 0.8       # share of the site's reported total we must account for
MIN_SPACING_S = 180      # minimum average gap between sessions on the SAME site
MAX_BACKOFF_DAYS = 14
ROBOTS_BACKOFF_DAYS = 7
log = logging.getLogger("beeroo.scrape")


# --------------------------------------------------------------------------
# State
# --------------------------------------------------------------------------


def _blank():
    return {"zones": {}, "blocked": {"consecutive": 0, "backoff_until": None}}


def load_state(path):
    """Per-retailer state. Migrates the old single-retailer format."""
    try:
        data = json.loads(Path(path).read_text())
    except (OSError, ValueError):
        return {"retailers": {}}
    if "retailers" not in data:  # v1: Dan Murphy's only
        data = {"retailers": {"dan_murphys": {"zones": data.get("zones", {}),
                                              "blocked": data.get("blocked") or _blank()["blocked"]}}}
    return data


def save_state(path, state):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, indent=2, sort_keys=True))
    tmp.replace(path)


def retailer_state(state, name):
    return state.setdefault("retailers", {}).setdefault(name, _blank())


def pick_zones(rstate, zones=ZONES, n=1):
    """Never-scraped zones first (in order), then the least recently successful."""
    def last(z):
        return rstate["zones"].get(z, {}).get("last_success") or ""
    return sorted(zones, key=lambda z: (last(z), zones.index(z)))[:n]


def pick_zone(rstate, zones=ZONES):
    return pick_zones(rstate, zones, 1)[0]


def backing_off(rstate, now):
    until = rstate.get("blocked", {}).get("backoff_until")
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
    from app import ingest
    from common import db

    def push(body):
        conn = db.connect(str(db_path))
        try:
            return ingest.ingest_trusted(conn, ingest.IngestIn.model_validate(body))
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


def _benign(errors):
    from app.ingest import BENIGN_ERRORS
    return sum(1 for _, reason in errors if reason in BENIGN_ERRORS)


def scrape_one(*, name, zone, scrape, push, rstate, now, max_pages):
    """One retailer x one state. Returns a result dict (never raises)."""
    from scrapers.bws_live import RobotsDisallow
    from scrapers.dan_murphys import Blocked

    entry = rstate["zones"].setdefault(zone, {})
    entry["last_attempt"] = now.isoformat()
    base = {"retailer": name, "zone": zone, "state": STATES[zone]["name"], "postcode": STATES[zone]["postcode"]}

    try:
        result = asyncio.run(scrape(STATES[zone]["postcode"], max_pages))
    except Blocked as e:
        n = rstate["blocked"].get("consecutive", 0) + 1
        days = min(2 ** n, MAX_BACKOFF_DAYS)
        rstate["blocked"] = {"consecutive": n, "backoff_until": (now + timedelta(days=days)).isoformat()}
        entry["last_error"] = f"blocked: {e}"
        log.error("%s BLOCKED (%s). Backing off %d day(s). Not retrying.", name, e, days)
        return {**base, "status": "blocked", "backoff_days": days}
    except RobotsDisallow as e:
        rstate["blocked"] = {"consecutive": rstate["blocked"].get("consecutive", 0),
                             "backoff_until": (now + timedelta(days=ROBOTS_BACKOFF_DAYS)).isoformat()}
        entry["last_error"] = f"robots.txt: {e}"
        log.error("%s: %s. Skipping for %d days.", name, e, ROBOTS_BACKOFF_DAYS)
        return {**base, "status": "robots_disallow", "backoff_days": ROBOTS_BACKOFF_DAYS}
    except Exception as e:  # noqa: BLE001 - unattended job: record, don't crash loudly
        entry["last_error"] = f"{type(e).__name__}: {e}"
        log.exception("%s scrape failed for %s", name, zone)
        return {**base, "status": "error", "error": entry["last_error"]}

    location, raw_pages = result["location"], result["raw_pages"]
    total, got = result.get("total"), result.get("collected", len(result["products"]))
    covered = got + _benign(result.get("errors", []))   # out-of-stock items are accounted for
    complete = bool(total) and covered >= MIN_COMPLETE * total

    pushed = {"products": 0, "new_observations": 0, "new_listings": 0}
    try:
        for page in raw_pages:
            r = push({
                "kind": KINDS[name],
                # Liquorland prices are per state and its location comes from the payload itself
                "location": None if name == "liquorland" else location_dict(location),
                "payload": page,
                "observed_at": now.isoformat(),
            })
            for k in pushed:
                pushed[k] += r.get(k, 0)
    except Exception as e:  # noqa: BLE001
        entry["last_error"] = f"push failed: {e}"
        log.exception("%s push failed", name)
        return {**base, "status": "push_failed", "error": str(e)}

    rstate["blocked"] = {"consecutive": 0, "backoff_until": None}
    entry.update(
        last_error=None, store=location.location_key, products=got, expected=total,
        complete=complete, **({"last_success": now.isoformat()} if complete else {}),
    )
    summary = {**base, "status": "ok" if complete else "partial", "store": location.location_key,
               "collected": got, "expected": total, **pushed}
    (log.info if complete else log.warning)("%s finished: %s", name, summary)
    return summary


def run(*, state_path, scrapes, push, now=None, retailers=None, zones_per_run=len(ZONES), zone=None,
        max_pages=60, zones=ZONES, sleep=time.sleep, monotonic=time.monotonic, jitter=0,
        min_spacing=MIN_SPACING_S):
    """Returns (exit_code, summary). `scrapes` maps retailer name -> async
    callable (postcode, max_pages) returning dict(location, raw_pages, products,
    errors, total[, collected]); it raises Blocked / RobotsDisallow on bot
    protection.

    Sessions are interleaved across retailers (BWS NSW, Liquorland NSW, DM NSW,
    BWS ACT, ...) so each site is naturally spaced by the others' sessions;
    `min_spacing` is enforced on top for any site visited back to back."""
    now = now or datetime.now(timezone.utc)
    state = load_state(state_path)
    names = [n for n in (retailers or RETAILERS) if n in scrapes]
    results, plan = [], {}

    for name in names:
        rstate = retailer_state(state, name)
        until = backing_off(rstate, now)
        if until:
            log.info("%s: backing off until %s; skipping", name, until.isoformat())
            results.append({"retailer": name, "status": "backing_off", "until": until.isoformat()})
            continue
        plan[name] = [zone] if zone else pick_zones(rstate, zones, zones_per_run)

    stopped, last_end, started = set(), {}, False

    for i in range(max((len(v) for v in plan.values()), default=0)):
        for name in names:
            if name not in plan or name in stopped or i >= len(plan[name]):
                continue

            if not started and jitter:
                sleep(random.uniform(0, jitter))
            started = True

            if name in last_end and min_spacing:
                wait = min_spacing * random.uniform(0.75, 1.25) - (monotonic() - last_end[name])
                if wait > 0:
                    sleep(wait)

            rstate = retailer_state(state, name)
            result = scrape_one(name=name, zone=plan[name][i], scrape=scrapes[name], push=push,
                                rstate=rstate, now=now, max_pages=max_pages)
            last_end[name] = monotonic()
            results.append(result)
            save_state(state_path, state)

            if result["status"] in ("blocked", "robots_disallow", "error", "push_failed"):
                stopped.add(name)   # this retailer is done for today; the others carry on

    save_state(state_path, state)
    statuses = {r["status"] for r in results}
    code = 3 if "blocked" in statuses else 1 if statuses & {"error", "push_failed", "robots_disallow"} else 0
    return code, {"results": results}


def browser_scrape(driver_scrape, browser_path=None):
    """Wrap a driver (dan_murphys.scrape / bws_live.scrape) in a visible browser."""
    async def scrape(postcode, max_pages):
        from playwright.async_api import async_playwright

        raw = []
        async with async_playwright() as p:
            # Headed: headless is blocked by Dan Murphy's and is a worse citizen anyway.
            browser = await p.chromium.launch(headless=False, executable_path=browser_path)
            try:
                context = await browser.new_context(
                    viewport={"width": 1440, "height": 900}, locale="en-AU", timezone_id="Australia/Sydney",
                )
                page = await context.new_page()
                location, products, errors, total, *extra = await driver_scrape(page, postcode, max_pages, raw_pages=raw)
            finally:
                await browser.close()
        result = {"location": location, "raw_pages": raw, "products": products, "errors": errors, "total": total}
        if extra:
            result.update(extra[0])
        return result

    return scrape


def real_scrapes(browser_path=None):
    from scrapers import bws_live, dan_murphys, liquorland_live
    return {
        "bws": browser_scrape(bws_live.scrape, browser_path),
        "liquorland": browser_scrape(liquorland_live.scrape, browser_path),
        "dan_murphys": browser_scrape(dan_murphys.scrape, browser_path),
    }


def setup_logging(path):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fmt = logging.Formatter("%(asctime)s %(levelname)s %(message)s")
    for handler in (RotatingFileHandler(path, maxBytes=500_000, backupCount=3), logging.StreamHandler()):
        handler.setFormatter(fmt)
        log.addHandler(handler)
    log.setLevel(logging.INFO)


def skipped_retailers(environ=os.environ):
    return {x.strip() for x in environ.get("BEEROO_SKIP_RETAILERS", "").split(",") if x.strip()}


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--retailer", choices=["all", *RETAILERS], default="all")
    ap.add_argument("--zones-per-run", type=int, default=len(ZONES), choices=range(1, len(ZONES) + 1),
                    help=f"states per retailer per run (default {len(ZONES)} = every state and territory)")
    ap.add_argument("--zone", choices=ZONES, help="force one state/territory (testing)")
    ap.add_argument("--push", action="store_true", help="push to $BEEROO_SERVER using $BEEROO_ADMIN_TOKEN (default: local DB)")
    ap.add_argument("--db", default=str(DEFAULT_DB))
    ap.add_argument("--state", default=str(DEFAULT_STATE))
    ap.add_argument("--max-pages", type=int, default=60)
    ap.add_argument("--jitter", type=int, default=0, help="random start delay up to N seconds")
    ap.add_argument("--min-spacing", type=int, default=MIN_SPACING_S, help="min average seconds between sessions on the same site")
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

    skip = skipped_retailers()
    chosen = list(RETAILERS) if args.retailer == "all" else [args.retailer]
    chosen = [r for r in chosen if r not in skip]
    if skip & set(RETAILERS):
        log.info("skipping (BEEROO_SKIP_RETAILERS): %s", ", ".join(sorted(skip & set(RETAILERS))))

    code, summary = run(
        state_path=args.state, scrapes=real_scrapes(args.browser_path), push=push,
        retailers=chosen, zones_per_run=args.zones_per_run, zone=args.zone,
        max_pages=args.max_pages, jitter=args.jitter, min_spacing=args.min_spacing,
    )
    print(json.dumps(summary))
    return code


if __name__ == "__main__":
    sys.exit(main())
