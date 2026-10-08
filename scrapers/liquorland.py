"""Liquorland parsing (pure functions, tested against saved captures).

The category list returns one entry per pack variant ("<sku>_ea",
"<sku>_pack6", "<sku>_ctn24"), each with its own price. Prices and range are
set per *state* (site state ll_act, ll_wa, ...), not per store, so the
location key is state-level. ABV is not in the list; it comes from the
per-product detail response (alcoholPercent).

NOTE: Liquorland's robots.txt disallows /api/*, where this data comes from. The
live driver (scrapers/liquorland_live.py) reads it from a normal visible browser
page at the operator's request, never solves or evades a CAPTCHA, and can be
switched off with BEEROO_SKIP_RETAILERS=liquorland. See docs/SCHEDULED_SCRAPE.md.
"""
import re

from common.records import (
    pack_type_for_units,
    Listing,
    Location,
    PackType,
    PriceObservation,
    Retailer,
    ScrapedProduct,
    utcnow,
)
from common.units import parse_abv

BASE_URL = "https://www.liquorland.com.au"

_UOM_RE = re.compile(r"^(ea|pack|ctn)(\d*)$", re.IGNORECASE)
_MULTIBUY_RE = re.compile(r"^(\d+)\s+for\s+\$(\d+(?:\.\d{1,2})?)$", re.IGNORECASE)


def site_state(payload):
    """'ll_act' from the response's debugQuery, or None."""
    match = re.search(r"sitestate=(\w+)", (payload or {}).get("debugQuery") or "")
    return match.group(1) if match else None


def clean_store_name(value):
    """The store the scraper picked in the location modal, made safe to show (None if unusable)."""
    text = re.sub(r"[\x00-\x1f\x7f<>]", " ", str(value or ""))
    text = re.sub(r"\s+", " ", text).strip()[:80]
    return text or None


def location_for_site(site, store_name=None):
    """State-level Location, e.g. 'll_wa' -> key 'liquorland:ll_wa'. Prices are per state, but
    `store_name` (the nearest store the scraper picked) says which store the visit used."""
    state = site.split("_", 1)[-1].upper()
    return Location(
        retailer=Retailer.LIQUORLAND,
        store_id=site,
        store_name=clean_store_name(store_name) or f"Liquorland {state} (state pricing)",
        state=state,
    )


def _units_and_type(uom):
    match = _UOM_RE.match((uom or "").strip())

    if not match:
        raise ValueError(f"unrecognised unit of measure {uom!r}")

    kind, count = match.group(1).lower(), match.group(2)

    if kind == "ea":
        return 1, PackType.SINGLE

    if not count:
        raise ValueError(f"no pack size in {uom!r}")

    return int(count), PackType.CASE if kind == "ctn" else pack_type_for_units(int(count))


def _entry_prices(entry, location_key, observed_at):
    units, pack_type = _units_and_type(entry.get("unitOfMeasure"))
    price = entry.get("price") or {}
    observations = []

    def add(value, total_units, member_only=False, kind=None):
        observations.append(
            PriceObservation(
                pack_type=kind or pack_type,
                units=total_units,
                price=float(value),
                member_only=member_only,
                location_key=location_key,
                observed_at=observed_at,
            )
        )

    if price.get("current"):
        add(price["current"], units)

    if price.get("memberOnlyPrice"):
        add(price["memberOnlyPrice"], units, member_only=True)

    callout = ((entry.get("promotion") or {}).get("calloutText") or "").strip()
    multibuy = _MULTIBUY_RE.match(callout)

    if multibuy:
        count = int(multibuy.group(1))
        add(
            multibuy.group(2),
            count * units,
            kind=pack_type_for_units(count * units),
        )

    return observations


def parse_liquorland_payload(payload, location_key=None, observed_at=None):
    """Parse one category-list response. Returns (products, errors).

    Pack variants of the same sku are merged into one ScrapedProduct.
    Unavailable entries are skipped.
    """
    observed_at = observed_at or utcnow()

    if location_key is None:
        site = site_state(payload)

        if site is None:
            raise ValueError("no sitestate in response; pass location_key")

        location_key = location_for_site(site).location_key

    by_sku, errors = {}, []

    for entry in payload.get("products") or []:
        entry_id = entry.get("id") or ""
        sku = entry_id.split("_")[0]

        try:
            if not sku:
                raise ValueError("missing id")

            if not entry.get("isAvailable"):
                raise ValueError("unavailable")

            prices = _entry_prices(entry, location_key, observed_at)

            if not prices:
                raise ValueError("no price")

            if sku not in by_sku:
                path = (entry.get("productUrl") or "").split("?")[0]
                by_sku[sku] = ScrapedProduct(
                    listing=Listing(
                        retailer=Retailer.LIQUORLAND,
                        retailer_sku=sku,
                        url=f"{BASE_URL}{path}",
                        name=entry["name"],
                        brand=entry.get("brand") or None,
                        category="beer",
                        abv=parse_abv(entry["name"]),
                        unit_volume_ml=entry.get("volumeMl") or None,
                    ),
                    prices=[],
                )

            by_sku[sku].prices.extend(prices)
        except (ValueError, KeyError) as e:
            errors.append((entry_id, str(e)))

    return list(by_sku.values()), errors


def parse_detail(payload):
    """ABV and the site's rounded standard drinks from a detail response."""
    product = (payload or {}).get("product") or {}
    abv = product.get("alcoholPercent")

    return {
        "sku": (product.get("id") or "").split("_")[0] or None,
        "abv": float(abv) if abv not in (None, "") else None,
        "site_standard_drinks": product.get("standardDrinks"),
    }


def apply_detail(listing, detail):
    """Return a copy of the listing with ABV filled in from the detail."""
    if detail.get("abv") is None:
        return listing

    return listing.model_copy(update={"abv": detail["abv"]})


def parse_suburb_search(payload):
    """[(suburb, state, postcode)] from SuburbSearch."""
    return [
        (row["suburb"], row["state"], row["postcode"])
        for row in (payload or {}).get("data") or []
    ]


def minimal_payload(payload):
    """Only what the parser reads: the site state and per-entry price/size/url.
    (A full page is ~90 KB of images, ratings and navigation.)"""
    site = site_state(payload)
    return {
        "debugQuery": f"sitestate={site}" if site else "",
        "meta": {"page": {k: ((payload.get("meta") or {}).get("page") or {}).get(k)
                           for k in ("current", "total", "size", "productCount")}},
        "products": [
            {
                **{k: p[k] for k in ("id", "name", "brand", "isAvailable", "volumeMl", "unitOfMeasure", "productUrl") if k in p},
                "price": {k: (p.get("price") or {}).get(k)
                          for k in ("current", "normal", "acrossAnySix", "memberOnlyPrice")},
                "promotion": {"calloutText": (p.get("promotion") or {}).get("calloutText")},
            }
            for p in payload.get("products") or []
        ],
    }
