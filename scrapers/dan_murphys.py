"""Dan Murphy's scraper.

Reads the structured JSON the category page itself loads (POST
/apis/ui/Browse) instead of parsing display text. We never call the API
directly: the browser loads the page and clicks "Load more" like a user,
and we passively read the responses. Location (postcode -> store) is set
through the site's own store selector.
"""
import asyncio
import json
import logging
import os
import random
import re
from urllib.parse import urlsplit

from playwright.async_api import TimeoutError as PlaywrightTimeoutError

from common.records import Location, Retailer, utcnow
from scrapers import dan_murphys_dom
from scrapers.endeavour import (  # noqa: F401  (re-exported for callers/tests)
    parse_browse_payload,
    parse_prices,
    parse_product,
)

BASE_URL = "https://www.danmurphys.com.au"
CATEGORY_URL = f"{BASE_URL}/beer/all"
BROWSE_PATH = "/apis/ui/Browse"
CATEGORY = "beer"

LOAD_MORE = ".infinite-loader__load-more-button"
BUTTON_WAIT_MS = 10000      # the button renders a moment after the page's data arrives
PAUSE_RANGE_S = (2.0, 4.0)
MAX_PAGES = 40



log = logging.getLogger("beeroo.scrape")


class Blocked(RuntimeError):
    """The site served a bot-protection page instead of content."""


# --------------------------------------------------------------------------
# Browser driving
# --------------------------------------------------------------------------


class BrowseCollector:
    """Passively records Browse responses the page makes."""

    def __init__(self):
        self.pages = {}
        self.total = None
        self.preferences = None
        self._changed = asyncio.Event()

    async def on_response(self, response):
        path = urlsplit(response.url).path

        try:
            if path.endswith(BROWSE_PATH) and response.request.method == "POST":
                payload = await response.json()
                request = json.loads(response.request.post_data or "{}")
                number = request.get("pageNumber", len(self.pages) + 1)
                self.pages[number] = payload
                self.total = payload.get("TotalRecordCount", self.total)
                self._changed.set()
            elif path.endswith("/Fulfilment/Preferences"):
                self.preferences = await response.json()
        except Exception:
            # non-JSON / aborted responses are not interesting
            pass

    async def wait_for_new_page(self, timeout_s=20):
        self._changed.clear()
        await asyncio.wait_for(self._changed.wait(), timeout_s)

    def reset_pages(self):
        self.pages.clear()
        self.total = None


async def raise_if_blocked(page, response=None):
    """Raise Blocked if the page is a bot-protection / CAPTCHA page. We never try
    to solve or get past these: the caller stops and backs off."""
    title = await page.title()
    url = page.url or ""

    if (
        (response is not None and response.status in (403, 429))
        or "Attention Required" in title
        or "captcha" in title.lower()
        or "perfdrive" in url            # Liquorland's ShieldSquare challenge host
    ):
        raise Blocked(f"bot protection page served (title={title!r})")

    try:
        text = (await page.inner_text("body"))[:3000].lower()
    except Exception:  # noqa: BLE001 - page may be mid-navigation; the title/url checks above still ran
        return

    if "think that you are a bot" in text or "solve this captcha" in text or "shieldsquare" in title.lower():
        raise Blocked(f"bot protection page served (title={title!r})")


async def open_category(page, collector):
    collector.reset_pages()
    response = await page.goto(
        CATEGORY_URL, wait_until="domcontentloaded", timeout=60000
    )
    await raise_if_blocked(page, response)

    try:
        await collector.wait_for_new_page(30)
    except asyncio.TimeoutError:
        await raise_if_blocked(page)
        # No JSON seen: fine if the cards are on the page (they are read as text instead).
        try:
            await page.locator("shop-product-card").first.wait_for(state="attached", timeout=30000)
        except PlaywrightTimeoutError:
            await raise_if_blocked(page)
            raise RuntimeError("category page loaded but no Browse response or product cards seen")


def location_from_preferences(preferences):
    details = (preferences or {}).get("ClickAndCollectDetails") or {}
    store_id = details.get("FulfilmentStoreID")

    if not store_id:
        return None

    return Location(
        retailer=Retailer.DAN_MURPHYS,
        store_id=str(store_id),
        store_name=details.get("FulfilmentStoreName"),
        suburb=(details.get("AddressSuburb") or "").title() or None,
        state=details.get("AddressState"),
        postcode=details.get("AddressPostalCode"),
    )


async def select_location(page, collector, postcode):
    """Use the site's own store selector to pick the nearest store."""
    visible = "visible=true"
    await page.get_by_text(
        re.compile(r"(Pick up|Delivery):")
    ).locator(visible).first.click()
    await page.get_by_text("Pick up from").locator(visible).first.wait_for()
    await page.get_by_text("Change", exact=True).locator(visible).first.click()

    box = page.get_by_placeholder("Start typing...").locator(visible)
    await box.click()
    await box.type(postcode, delay=120)

    suggestion = page.get_by_text(
        re.compile(rf",\s*[A-Z]{{2,3}},\s*{re.escape(postcode)}$")
    ).locator(visible).first

    try:
        await suggestion.click(timeout=10000)
    except PlaywrightTimeoutError:
        raise LookupError(f"no Dan Murphy's location found for {postcode}")

    await page.wait_for_timeout(2500)
    collector.preferences = None
    await page.get_by_text(re.compile(r"Km away")).locator(visible).first.click()
    await page.wait_for_timeout(4000)

    location = location_from_preferences(collector.preferences)

    if location is None:
        # fall back to reading the selection back from the server
        await open_category(page, collector)
        location = location_from_preferences(collector.preferences)

    return location


async def load_more_button(page, wait_ms=BUTTON_WAIT_MS):
    """The "Load more" button once it has rendered, or None if it doesn't show up
    (the list is complete). Checking at once, before the page has drawn it, is what
    cut a cloud run short after the first 24 of 392 products."""
    button = page.locator(LOAD_MORE).first
    try:
        await button.wait_for(state="visible", timeout=wait_ms)
    except PlaywrightTimeoutError:
        return None
    return button


async def click_load_more(button):
    for attempt in (1, 2):
        try:
            await button.scroll_into_view_if_needed()
            await button.click(timeout=10000)
            return True
        except PlaywrightTimeoutError:
            if attempt == 2:
                return False
            await asyncio.sleep(1)
    return False


async def load_all_pages(page, collector, max_pages=MAX_PAGES):
    while len(collector.pages) < max_pages:
        loaded = sum(
            len(b.get("Products") or [])
            for payload in collector.pages.values()
            for b in payload.get("Bundles") or []
        )

        if collector.total is not None and loaded >= collector.total:
            break

        button = await load_more_button(page)

        if button is None:
            log.warning("no 'Load more' button after %d s with %d of %s products loaded",
                        BUTTON_WAIT_MS // 1000, loaded, collector.total)
            break

        await asyncio.sleep(random.uniform(*PAUSE_RANGE_S))

        if not await click_load_more(button):
            log.warning("could not click 'Load more' with %d of %s products loaded", loaded, collector.total)
            break

        try:
            await collector.wait_for_new_page()
        except asyncio.TimeoutError:
            log.warning("'Load more' clicked but no new page of products arrived (%d of %s loaded)", loaded, collector.total)
            break


_CARDS_JS = """() => [...document.querySelectorAll('shop-product-card')].map(card => {
  const link = card.querySelector('a[href*="/product/"]');
  const stars = card.querySelector('shop-star-rating');
  let rating = null;
  if (stars) {
    const partial = stars.querySelector('.half-star-rating');
    rating = Math.round((stars.querySelectorAll('.rating-icon.checked').length
      + (partial ? parseFloat(partial.style.width) / 100 || 0 : 0)) * 100) / 100;
  }
  return {href: link ? link.getAttribute('href') : (card.getAttribute('data-url') || ''),
          rating: rating, lines: card.innerText.split('\\n')};
})"""


async def read_cards(page):
    """What a visitor sees on each product card: link + text lines."""
    return await page.evaluate(_CARDS_JS)


async def load_all_cards(page, max_clicks=100):
    """Click "Load more" like a user until the card count stops growing."""
    cards = page.locator("shop-product-card")
    try:
        await cards.first.wait_for(state="attached", timeout=15000)
    except PlaywrightTimeoutError:
        return
    previous = 0

    for _ in range(max_clicks):
        current = await cards.count()
        if current == previous:
            await page.wait_for_timeout(1000)
            current = await cards.count()
            if current == previous:
                break
        previous = current

        button = await load_more_button(page)
        if button is None:
            break

        await asyncio.sleep(random.uniform(*PAUSE_RANGE_S))
        if not await click_load_more(button):
            break
        await page.wait_for_timeout(1500)


def collect_products(collector, location_key, observed_at=None, ratings=None):
    observed_at = observed_at or utcnow()
    by_sku, errors = {}, []

    for number in sorted(collector.pages):
        page = {**collector.pages[number], "ratings": ratings} if ratings else collector.pages[number]
        products, errs = parse_browse_payload(page, location_key, observed_at)
        errors.extend(errs)

        for product in products:
            by_sku.setdefault(product.listing.retailer_sku, product)

    return list(by_sku.values()), errors


async def scrape(page, postcode, max_pages=MAX_PAGES, raw_pages=None):
    """Returns (location, products, errors, expected_total).

    If `raw_pages` is a list, the raw Browse payloads (in page order) are
    appended to it so callers can forward them to a server for parsing."""
    collector = BrowseCollector()
    page.on(
        "response", lambda r: asyncio.ensure_future(collector.on_response(r))
    )

    await open_category(page, collector)

    if postcode:
        try:
            location = await select_location(page, collector, postcode)
        except Exception:
            await page.screenshot(path="data/location_debug.png")
            raise
        await open_category(page, collector)  # reload with chosen store
    else:
        location = location_from_preferences(collector.preferences)

    if location is None:
        raise RuntimeError("could not determine the active store")

    # BEEROO_DM_METHOD: auto (the page's JSON if seen, else the cards' text), json, or cards
    method = os.environ.get("BEEROO_DM_METHOD", "auto")
    if method not in {"auto", "json", "cards"}:
        raise ValueError("BEEROO_DM_METHOD must be auto, json or cards")

    if method == "cards" or (method == "auto" and not collector.pages):
        await load_all_cards(page)
        cards = await read_cards(page)
        products, errors = dan_murphys_dom.parse_cards_payload({"cards": cards}, location.location_key)
        if raw_pages is not None:
            raw_pages.append({"cards": cards})
        return (location, products, errors, dan_murphys_dom.count_unique(cards),
                {"kind": "dan_murphys_cards"})

    await load_all_pages(page, collector, max_pages)

    # The Browse JSON says every product has 0 reviews; the stars on the page are the real ones.
    try:
        ratings = dan_murphys_dom.ratings_from_cards(await read_cards(page))
    except Exception:  # noqa: BLE001 - ratings are a bonus; never lose prices over them
        log.warning("could not read the star ratings from the page", exc_info=True)
        ratings = {}

    products, errors = collect_products(collector, location.location_key, ratings=ratings)

    if raw_pages is not None:
        raw_pages.extend({**collector.pages[n], "ratings": ratings} if ratings else collector.pages[n]
                         for n in sorted(collector.pages))

    return location, products, errors, collector.total
