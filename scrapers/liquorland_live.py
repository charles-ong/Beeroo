"""Liquorland live driver: a normal, visible browser reads the product data the
beer page itself loads. We never call Liquorland's APIs directly.

How the site works (observed 2026-10-01):
  * https://www.liquorland.com.au/beer-and-cider/beer shows ~816 beers & ciders,
    60 per page (~14 pages). The page calls
    GET /api/products/ll/<state>/beer-and-cider?page=N&show=60&facets=beer.
  * Prices/range are per STATE ("ll/act", "ll/nsw", ...), chosen via a modal
    "See what's available near you" (postcode or suburb -> suggestion ->
    "Save location"). A "Show more" button loads the next page.

IMPORTANT: Liquorland's robots.txt disallows /api/*, the path this data comes
from, and the site shows a CAPTCHA to some automation. We obey the page-level
robots rules, never solve or evade a CAPTCHA (we stop and back off), and the
operator opts in knowingly (docs/SCHEDULED_SCRAPE.md).
"""
import asyncio
import logging
import random
import re
from urllib.parse import urlsplit

from common.records import utcnow
from scrapers import liquorland
from scrapers.bws_live import RobotsDisallow, path_allowed
from scrapers.dan_murphys import Blocked, raise_if_blocked

log = logging.getLogger("beeroo.scrape")

BASE_URL = "https://www.liquorland.com.au"
ROBOTS_URL = f"{BASE_URL}/robots.txt"
CATEGORY_PATH = "/beer-and-cider/beer"
CATEGORY_URL = BASE_URL + CATEGORY_PATH
PAUSE_RANGE_S = (2.0, 4.0)
MAX_PAGES = 30
_LIST_PATH = re.compile(r"^/api/products/ll/([a-z]+)/beer-and-cider/?$")


def page_number(query, payload, default):
    """Which page this response is. Prefer the request's own parameters
    (`page=N`, or `fh_start_index=60*(N-1)`); meta.page.current can repeat."""
    from urllib.parse import parse_qs

    q = parse_qs(query)
    try:
        if q.get("page"):
            return int(q["page"][0])
        if q.get("fh_start_index"):
            size = int((q.get("show") or q.get("fh_view_size") or ["60"])[0])
            return int(q["fh_start_index"][0]) // size + 1
    except (ValueError, ZeroDivisionError):
        pass
    return ((payload.get("meta") or {}).get("page") or {}).get("current") or default


class LiquorlandCollector:
    """Passively records the category list pages the site loads."""

    def __init__(self):
        self.pages = {}      # (state_code, page_number) -> payload
        self.total = None
        self.nearby = None   # latest "find nearby stores" response

    async def on_response(self, response):
        try:
            u = urlsplit(response.url)
            if u.path.startswith("/api/inventory/ll/findnearby/"):
                self.nearby = await response.json()
                return
            match = _LIST_PATH.match(u.path)
            if not match or response.request.method != "GET":
                return
            payload = await response.json()
            site = liquorland.site_state(payload) or f"ll_{match.group(1)}"
            number = page_number(u.query, payload, len(self.pages) + 1)
            self.pages[(site, number)] = payload
            self.total = ((payload.get("meta") or {}).get("page") or {}).get("productCount", self.total)
        except Exception:
            pass  # non-JSON / aborted

    def for_site(self, site):
        return [self.pages[k] for k in sorted(self.pages, key=lambda k: k[1]) if k[0] == site]

    def reset(self):
        self.pages.clear()
        self.total = None


async def check_robots(page):
    """Load robots.txt in the same visible browser; refuse a disallowed PAGE path.
    (The /api/ data fetches are disallowed too; see the module docstring.)"""
    response = await page.goto(ROBOTS_URL, wait_until="domcontentloaded", timeout=45000)
    await raise_if_blocked(page, response)
    text = await page.inner_text("body")
    if not path_allowed(text, CATEGORY_PATH):
        raise RobotsDisallow(f"robots.txt disallows {CATEGORY_PATH}")


async def select_state(page, collector, postcode):
    """Use the site's own location modal. The modal normally opens on first visit."""
    box = page.locator("input[aria-label^='Enter a suburb or postcode']").locator("visible=true").first
    if not await box.count():
        await page.get_by_text("Set shopping method").locator("visible=true").first.click()
        await page.wait_for_timeout(1500)
        box = page.locator("input[aria-label^='Enter a suburb or postcode']").locator("visible=true").first
    await box.click(timeout=15000)
    await box.type(postcode, delay=130)
    await page.wait_for_timeout(2500)

    suggestion = page.locator("button.search-results-item").filter(has_text=re.compile(rf"\b{re.escape(postcode)}\b")).first
    try:
        await suggestion.click(timeout=10000)
    except Exception:
        raise LookupError(f"no Liquorland location found for {postcode}")
    await page.wait_for_timeout(1000)
    await page.get_by_role("button", name=re.compile(r"save location", re.I)).locator("visible=true").first.click(timeout=15000)
    await page.wait_for_timeout(3000)

    # "Set shopping method" drawer: Click & Collect is preselected and lists stores
    # (div.StoreItem[role=button]) nearest first. Picking the first commits the
    # location, and with it the state's prices.
    row = page.locator("div.StoreItem").locator("visible=true").first
    await row.wait_for(timeout=15000)
    name = (await row.locator(".store-name").inner_text()).strip()
    await row.click()
    await page.wait_for_timeout(1500)
    await page.get_by_role("button", name=re.compile(r"save\s*&\s*continue", re.I)).locator("visible=true").first.click(timeout=15000)
    await page.wait_for_timeout(5000)
    return name


async def load_all_pages(page, collector, site, max_pages=MAX_PAGES):
    """Page through the list like a user: pick 80 results per page (a normal
    on-page control), then follow "Go to next page (N)" until there isn't one."""
    per_page = page.get_by_role("button", name=re.compile(r"show 80 results per page", re.I)).locator("visible=true").first
    if await per_page.count():
        before = len(collector.for_site(site))
        await per_page.click()
        await page.wait_for_timeout(3500)
        log.debug("80 per page: pages seen %d -> %d", before, len(collector.for_site(site)))

    nxt = page.get_by_role("link", name=re.compile(r"go to next page", re.I)).locator("visible=true")
    for _ in range(max_pages):
        if not await nxt.count():
            break
        have = len(collector.for_site(site))
        await asyncio.sleep(random.uniform(*PAUSE_RANGE_S))
        await nxt.first.click()
        await page.wait_for_timeout(3000)
        if len(collector.for_site(site)) <= have:
            await page.wait_for_timeout(3000)            # one patient retry
            if len(collector.for_site(site)) <= have:
                log.debug("next page produced no new data; stopping")
                break


async def scrape(page, postcode, max_pages=MAX_PAGES, raw_pages=None, expected_state=None):
    """Returns (location, products, errors, expected_total, extra) where extra =
    {"collected": entries}. Otherwise like the other drivers.
    `expected_state` (e.g. "NSW") guards against data from the wrong state."""
    from common.postcodes import state_for_postcode

    expected_state = expected_state or state_for_postcode(postcode)
    site = f"ll_{expected_state.lower()}"
    collector = LiquorlandCollector()
    page.on("response", lambda r: asyncio.ensure_future(collector.on_response(r)))

    await check_robots(page)
    response = await page.goto(CATEGORY_URL, wait_until="domcontentloaded", timeout=60000)
    await raise_if_blocked(page, response)
    await page.wait_for_timeout(5000)

    try:
        store_name = await select_state(page, collector, postcode)
    except Exception:
        await page.screenshot(path="data/liquorland_location_debug.png")
        raise

    if not collector.for_site(site):
        # not switched live: reload so the saved location is applied
        collector.reset()
        response = await page.goto(CATEGORY_URL, wait_until="domcontentloaded", timeout=60000)
        await raise_if_blocked(page, response)
        await page.wait_for_timeout(6000)

    if not collector.for_site(site):
        seen = sorted({k[0] for k in collector.pages})
        raise RuntimeError(f"Liquorland did not switch to {site} (saw {seen or 'no list data'})")

    await load_all_pages(page, collector, site, max_pages)

    pages = collector.for_site(site)
    seen, unique = set(), []
    for pg in pages:
        for p in pg.get("products") or []:
            if p.get("id") not in seen:
                seen.add(p.get("id"))
                unique.append(p)
    merged = {"debugQuery": f"sitestate={site}", "products": unique}
    location = liquorland.location_for_site(site)
    products, errors = liquorland.parse_liquorland_payload(merged, location.location_key, utcnow())
    if raw_pages is not None:
        raw_pages.extend(liquorland.minimal_payload(pg) for pg in pages)
    # `collected` = list entries gathered (each pack variant is an entry), the
    # unit the site's total is counted in; products are those entries merged by SKU.
    return location, products, errors, collector.total or len(unique), {"collected": len(unique)}
