"""BWS live driver: a normal, visible browser reads the product data the beer
page itself loads. We never call BWS's APIs directly.

How the site works (observed 2026-10-01):
  * https://bws.com.au/beer/all-beer lists every beer. The page calls
    GET /apis/ui/Browse?Department=beer&Location=...&PageNumber=N, then fetches
    full product data in chunks via GET /apis/ui/Products/Bundles/<12 ids>, whose
    response is a list of bundles (same shape as ProductGroup "Items").
  * The store is chosen in a modal ("Set Your Store" -> "Enter postcode or
    suburb"), which POSTs /apis/ui/Address/SetPickupByStoreNo.
robots.txt (checked via the same browser) allows category pages; it disallows
/search, /my-account, /checkout, /customer-reviews.
"""
import asyncio
import json
import logging
import random
import re
from urllib.parse import urlsplit

from common.records import Retailer, utcnow
from scrapers import bws
from scrapers.dan_murphys import Blocked, raise_if_blocked  # shared bot-protection detection

log = logging.getLogger("beeroo.scrape")

BASE_URL = "https://bws.com.au"
ROBOTS_URL = "https://www.bws.com.au/robots.txt"
CATEGORY_URL = f"{BASE_URL}/beer/all-beer"
PAUSE_RANGE_S = (2.0, 4.0)
MAX_ROUNDS = 60
IDLE_ROUNDS = 3  # stop after this many rounds with no new products


class RobotsDisallow(RuntimeError):
    """robots.txt forbids the page we were about to load."""


def path_allowed(robots_text, path, agent="*"):
    """Minimal robots.txt check for one user-agent group (supports Allow/Disallow
    prefixes; longest match wins; no wildcards needed for this site)."""
    rules, applies = [], False
    for raw in robots_text.splitlines():
        line = raw.split("#", 1)[0].strip()
        if ":" not in line:
            continue
        key, value = (x.strip() for x in line.split(":", 1))
        key = key.lower()
        if key == "user-agent":
            applies = value == "*" or value.lower() == agent.lower()
        elif applies and key in ("allow", "disallow") and value:
            rules.append((key, value))
    best = max((r for r in rules if path.startswith(r[1])), key=lambda r: len(r[1]), default=None)
    return best is None or best[0] == "allow"


async def wait_for(getter, timeout_s=15.0, step_s=0.5, sleep=asyncio.sleep):
    """Poll `getter()` until it returns something truthy or the timeout passes."""
    waited = 0.0
    while waited < timeout_s:
        value = getter()
        if value:
            return value
        await sleep(step_s)
        waited += step_s
    return getter()


class BwsCollector:
    """Passively records the product responses the page loads."""

    def __init__(self):
        self.items = {}          # PackParentStockCode -> item
        self.preferences = None  # SetPickupByStoreNo response
        self.default_store = None
        self.browse_pages = 0
        self.total = None
        self._changed = asyncio.Event()

    async def on_response(self, response):
        u = urlsplit(response.url)
        path = u.path
        try:
            if "/apis/ui/Products/Bundles/" in path and response.request.method == "GET":
                bundles = await response.json()
                for b in bundles or []:
                    if b.get("Products"):
                        self.items[b["PackParentStockCode"]] = b
                self._changed.set()
            elif path.endswith("/apis/ui/ProductGroup/Products/" + path.rsplit("/", 1)[-1]) and "ProductGroup/Products/" in path:
                payload = await response.json()
                for b in payload.get("Items") or []:
                    if b.get("Products"):
                        self.items[b["PackParentStockCode"]] = b
                self._changed.set()
            elif path.endswith("/apis/ui/Browse") and response.request.method == "GET":
                payload = await response.json()
                for b in payload.get("Bundles") or []:
                    if b.get("Products"):
                        self.items[b["PackParentStockCode"]] = b
                self.total = payload.get("TotalRecordCount", self.total)
                self.browse_pages += 1
                self._changed.set()
            elif path.endswith("/apis/ui/Address/SetPickupByStoreNo"):
                self.preferences = await response.json()
            elif path.endswith("/apis/ui/StoreLocator/Store"):
                self.default_store = await response.json()
        except Exception:
            pass  # non-JSON / aborted responses are not interesting

    def payload(self):
        return {"Items": list(self.items.values()), "TotalRecordCount": len(self.items)}


async def check_robots(page, path="/beer/all-beer"):
    """Fetch robots.txt with the same visible browser and refuse to continue if
    the target path is disallowed."""
    response = await page.goto(ROBOTS_URL, wait_until="domcontentloaded", timeout=45000)
    await raise_if_blocked(page, response)
    text = await page.inner_text("body")
    if not path_allowed(text, path):
        raise RobotsDisallow(f"robots.txt disallows {path}")


async def select_store(page, collector, postcode):
    """Use the site's own store modal. Returns the chosen Location."""
    visible = "visible=true"
    enter = page.get_by_text(re.compile(r"enter postcode or suburb", re.I)).locator(visible).first
    if not await enter.count():
        await page.get_by_text("Set Your Store").locator(visible).first.click()
        await page.wait_for_timeout(1500)
        enter = page.get_by_text(re.compile(r"enter postcode or suburb", re.I)).locator(visible).first
    await enter.click(timeout=15000)
    await page.wait_for_timeout(1000)

    box = page.locator("input[type=text]:visible, input[type=search]:visible, input:not([type]):visible").last
    await box.click()
    await box.type(postcode, delay=120)
    await page.wait_for_timeout(2500)

    suggestion = page.get_by_text(re.compile(rf"\b{re.escape(postcode)}\b")).locator(visible).first
    try:
        await suggestion.click(timeout=10000)
    except Exception:
        raise LookupError(f"no BWS location found for {postcode}")
    await page.wait_for_timeout(2500)

    # The modal lists nearby stores ("BWS Woden", ...), each with a SELECT button.
    collector.preferences = None
    select = page.get_by_text(re.compile(r"^\s*select\s*$", re.I)).locator(visible).first
    await select.click(timeout=15000)
    # wait for the site's own answer (slow responses used to be missed by a fixed 4 s sleep)
    if not await wait_for(lambda: collector.preferences):
        log.warning("no store response after SELECT; clicking once more")
        if await select.count():
            await select.click(timeout=10000)
        await wait_for(lambda: collector.preferences)
    await page.wait_for_timeout(1000)
    return bws.location_from_set_pickup(collector.preferences)


async def load_all(page, collector, max_rounds=MAX_ROUNDS):
    """Click "LOAD MORE" like a user until nothing new arrives. (It is an <a>,
    not a <button>: 40 products per click, ~745 beers => ~19 pages.)"""
    idle, last = 0, len(collector.items)
    more = page.get_by_text(re.compile(r"^\s*load more\s*$", re.I)).locator("visible=true")
    for _ in range(max_rounds):
        await asyncio.sleep(random.uniform(*PAUSE_RANGE_S))
        if await more.count():
            try:
                await more.first.click(timeout=10000)
            except Exception as e:  # noqa: BLE001
                # The link can vanish between the check and the click (last page, or the list
                # re-rendering). Keep what we have rather than lose the whole scrape.
                log.warning("LOAD MORE went away before it could be clicked (%s); carrying on", type(e).__name__)
        else:
            await page.mouse.wheel(0, 2500)   # fallback for lazy-loaded chunks
        await page.wait_for_timeout(2500)
        now = len(collector.items)
        log.debug("round: %d products so far", now)
        idle = 0 if now > last else idle + 1
        last = now
        if idle >= IDLE_ROUNDS and not await more.count():
            break


async def scrape(page, postcode, max_pages=MAX_ROUNDS, raw_pages=None):
    """Returns (location, products, errors, expected_total) like the Dan Murphy's driver."""
    collector = BwsCollector()
    page.on("response", lambda r: asyncio.ensure_future(collector.on_response(r)))

    await check_robots(page)
    response = await page.goto(CATEGORY_URL, wait_until="domcontentloaded", timeout=60000)
    await raise_if_blocked(page, response)
    await page.wait_for_timeout(6000)

    try:
        location = await select_store(page, collector, postcode)
    except Exception:
        await page.screenshot(path="data/bws_location_debug.png")
        raise
    if location is None:
        await page.screenshot(path="data/bws_location_debug.png")
        prefs = collector.preferences
        what = "no SetPickupByStoreNo response was seen" if prefs is None else (
            f"SetPickupByStoreNo returned Success={prefs.get('Success')!r} with keys {sorted(prefs)[:6]}")
        raise RuntimeError(f"could not determine the active BWS store ({what}); see data/bws_location_debug.png")

    collector.items.clear()
    response = await page.goto(CATEGORY_URL, wait_until="domcontentloaded", timeout=60000)
    await raise_if_blocked(page, response)
    await page.wait_for_timeout(6000)
    await load_all(page, collector, max_pages)

    payload = collector.payload()
    products, errors = bws.parse_bws_payload(payload, location.location_key, utcnow())
    if raw_pages is not None:
        raw_pages.append(bws.minimal_payload(payload))
    return location, products, errors, collector.total or len(collector.items)
