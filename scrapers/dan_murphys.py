"""Dan Murphy's scraper.

Reads the structured JSON the category page itself loads (POST
/apis/ui/Browse) instead of parsing display text. We never call the API
directly: the browser loads the page and clicks "Load more" like a user,
and we passively read the responses. Location (postcode -> store) is set
through the site's own store selector.
"""
import asyncio
import json
import random
import re
from urllib.parse import urlsplit

from playwright.async_api import TimeoutError as PlaywrightTimeoutError

from common.records import (
    Listing,
    Location,
    PackType,
    PriceObservation,
    Retailer,
    ScrapedProduct,
    utcnow,
)
from common.units import parse_abv, parse_volume_ml

BASE_URL = "https://www.danmurphys.com.au"
CATEGORY_URL = f"{BASE_URL}/beer/all"
BROWSE_PATH = "/apis/ui/Browse"
CATEGORY = "beer"

PAUSE_RANGE_S = (2.0, 4.0)
MAX_PAGES = 40

_UNITS_IN_BRACKETS = re.compile(r"\((\d+)\)")
_MULTIBUY_WORD = re.compile(r"for\s+\d+\s+(bottles?|cans?|packs?|cases?)", re.I)


class Blocked(RuntimeError):
    """The site served a bot-protection page instead of content."""


# --------------------------------------------------------------------------
# Parsing (pure functions; tested against saved fixtures)
# --------------------------------------------------------------------------


def _details(product):
    return {
        d.get("Name"): d.get("Value")
        for d in product.get("AdditionalDetails") or []
    }


def _clean(text):
    return re.sub(r"\s+", " ", (text or "").replace("<br>", " ")).strip()


def product_name(product, details):
    brand = _clean(details.get("webbrandname"))
    title = _clean(details.get("webtitle"))

    if title:
        if brand and not title.lower().startswith(brand.lower()):
            return f"{brand} {title}"
        return title

    return _clean(product.get("Description"))


def _pack_sizes(product):
    return {
        (p.get("Key") or "").lower(): p.get("UnitQty")
        for p in product.get("AvailablePackTypes") or []
        if p.get("UnitQty")
    }


def _units_and_type(message, price, pack_sizes):
    """Resolve total units and pack type for one price entry, or None."""
    message = (message or "").strip().lower()
    quantity = price.get("Quantity") or 0

    if quantity > 1:  # multi-buy: "$36 for 2 packs"
        word = _MULTIBUY_WORD.search(message)
        noun = word.group(1).rstrip("s") if word else ""
        per = {"case": pack_sizes.get("case"), "pack": pack_sizes.get("pack")}
        per_unit = per.get(noun, 1 if noun in {"bottle", "can"} else None)
        if not per_unit:
            return None
        units = quantity * per_unit
        return units, PackType.CASE if noun == "case" else PackType.PACK

    match = _UNITS_IN_BRACKETS.search(message)
    if match:
        units = int(match.group(1))
    elif message.startswith("each"):
        units = 1
    else:
        return None

    if (price.get("PackType") or "").lower() == "case":
        return units, PackType.CASE
    return units, PackType.SINGLE if units == 1 else PackType.PACK


def parse_prices(product, location_key, observed_at):
    """Online-purchasable price options.

    caseprice / singleprice hold the standard (non-member) price, whether or
    not a member offer exists. promoprice is the member / multi-buy offer.
    "in-store" prices are skipped (not purchasable online).
    """
    prices = product.get("Prices") or {}
    pack_sizes = _pack_sizes(product)
    observations = []

    for key, member_only in (
        ("caseprice", False),
        ("singleprice", False),
        ("promoprice", True),
    ):
        price = prices.get(key)

        if not price or not price.get("Value"):
            continue

        message = price.get("Message") or ""

        if "in-store" in message.lower():
            continue

        resolved = _units_and_type(message, price, pack_sizes)

        if resolved is None:
            raise ValueError(f"unrecognised price message {message!r}")

        units, pack_type = resolved
        observations.append(
            PriceObservation(
                pack_type=pack_type,
                units=units,
                price=float(price["Value"]),
                member_only=member_only,
                location_key=location_key,
                observed_at=observed_at,
            )
        )

    return observations


def parse_product(product, location_key, observed_at):
    details = _details(product)
    sku = str(product["Stockcode"])
    name = product_name(product, details)
    slug = product.get("UrlFriendlyName") or ""

    abv = parse_abv(details.get("webalcoholpercentage") or "")
    volume = parse_volume_ml(
        details.get("webliquorsize") or product.get("PackageSize") or ""
    )

    listing = Listing(
        retailer=Retailer.DAN_MURPHYS,
        retailer_sku=sku,
        url=f"{BASE_URL}/product/{sku}/{slug}".rstrip("/"),
        name=name,
        brand=_clean(details.get("webbrandname")) or None,
        category=details.get("webproducttype") or CATEGORY,
        abv=abv,
        unit_volume_ml=volume,
    )

    return ScrapedProduct(
        listing=listing,
        prices=parse_prices(product, location_key, observed_at),
    )


def parse_browse_payload(payload, location_key, observed_at=None):
    """Parse one Browse response. Returns (products, errors)."""
    observed_at = observed_at or utcnow()
    products, errors = [], []

    for bundle in payload.get("Bundles") or []:
        for raw in bundle.get("Products") or []:
            try:
                parsed = parse_product(raw, location_key, observed_at)
            except (ValueError, KeyError) as e:
                errors.append((raw.get("Stockcode"), str(e)))
                continue

            if not parsed.prices:
                errors.append((raw.get("Stockcode"), "no online prices"))
                continue

            products.append(parsed)

    return products, errors


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
    title = await page.title()

    if (response is not None and response.status == 403) or (
        "Attention Required" in title
    ):
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
        raise RuntimeError("category page loaded but no Browse response seen")


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


async def scrape(page, postcode, max_pages=MAX_PAGES):
    """Returns (location, products, errors, expected_total)."""
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

    await load_all_pages(page, collector, max_pages)
    products, errors = collect_products(collector, location.location_key)

    return location, products, errors, collector.total
