"""Dan Murphy's scraper.

Reads the structured JSON the category page itself loads (POST
/apis/ui/Browse) instead of parsing display text. We never call the API
directly: the browser loads the page and clicks "Load more" like a user,
and we passively read the responses. Location (postcode -> store) is set
through the site's own store selector.
"""
import asyncio
import json
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

PAUSE_RANGE_S = (2.0, 4.0)
MAX_PAGES = 40



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


async def load_all_pages(page, collector, max_pages=MAX_PAGES):
    while len(collector.pages) < max_pages:
        loaded = sum(
            len(b.get("Products") or [])
            for payload in collector.pages.values()
            for b in payload.get("Bundles") or []
        )

        if collector.total is not None and loaded >= collector.total:
            break

        button = page.locator(".infinite-loader__load-more-button")

        if await button.count() == 0 or not await button.is_visible():
            break

        await asyncio.sleep(random.uniform(*PAUSE_RANGE_S))
        await button.click()

        try:
            await collector.wait_for_new_page()
        except asyncio.TimeoutError:
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
    button = page.locator(".infinite-loader__load-more-button")
    previous = 0

    for _ in range(max_clicks):
        current = await cards.count()
        if current == previous:
            await page.wait_for_timeout(1000)
            current = await cards.count()
            if current == previous:
                break
        previous = current

        if not await button.count() or not await button.is_visible():
            break

        await asyncio.sleep(random.uniform(*PAUSE_RANGE_S))
        try:
            await button.scroll_into_view_if_needed()
            await button.click()
            await page.wait_for_timeout(1500)
        except Exception:  # noqa: BLE001 - the button can vanish at the end of the list
            break


def collect_products(collector, location_key, observed_at=None):
    observed_at = observed_at or utcnow()
    by_sku, errors = {}, []

    for number in sorted(collector.pages):
        products, errs = parse_browse_payload(
            collector.pages[number], location_key, observed_at
        )
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
    products, errors = collect_products(collector, location.location_key)

    if raw_pages is not None:
        raw_pages.extend(collector.pages[n] for n in sorted(collector.pages))

    return location, products, errors, collector.total
