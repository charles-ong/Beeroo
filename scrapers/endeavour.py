"""Parsing for Endeavour Group "Browse" JSON (Dan Murphy's, and BWS if it
shares the format - unverified, see docs/ACCESS_NOTES.md).

Pure functions only: no browser, no network. Tested against saved fixtures.
"""
import re

from common.records import (
    Listing,
    PackType,
    PriceObservation,
    Retailer,
    ScrapedProduct,
    utcnow,
)
from common.units import parse_abv, parse_volume_ml

DM_BASE_URL = "https://www.danmurphys.com.au"

_UNITS_IN_BRACKETS = re.compile(r"\((\d+)\)")
_MULTIBUY_WORD = re.compile(r"for\s+\d+\s+(bottles?|cans?|packs?|cases?)", re.I)


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


def parse_product(
    product, location_key, observed_at, retailer=Retailer.DAN_MURPHYS,
    base_url=DM_BASE_URL,
):
    details = _details(product)
    sku = str(product["Stockcode"])
    name = product_name(product, details)
    slug = product.get("UrlFriendlyName") or ""

    abv = parse_abv(details.get("webalcoholpercentage") or "")
    volume = parse_volume_ml(
        details.get("webliquorsize") or product.get("PackageSize") or ""
    )

    listing = Listing(
        retailer=retailer,
        retailer_sku=sku,
        url=f"{base_url}/product/{sku}/{slug}".rstrip("/"),
        name=name,
        brand=_clean(details.get("webbrandname")) or None,
        category=details.get("webproducttype") or "beer",
        abv=abv,
        unit_volume_ml=volume,
    )

    return ScrapedProduct(
        listing=listing,
        prices=parse_prices(product, location_key, observed_at),
    )


def parse_browse_payload(
    payload, location_key, observed_at=None, retailer=Retailer.DAN_MURPHYS,
    base_url=DM_BASE_URL,
):
    """Parse one Browse response. Returns (products, errors)."""
    observed_at = observed_at or utcnow()
    products, errors = [], []

    for bundle in payload.get("Bundles") or []:
        for raw in bundle.get("Products") or []:
            try:
                parsed = parse_product(
                    raw, location_key, observed_at, retailer, base_url
                )
            except (ValueError, KeyError) as e:
                errors.append((raw.get("Stockcode"), str(e)))
                continue

            if not parsed.prices:
                errors.append((raw.get("Stockcode"), "no online prices"))
                continue

            products.append(parsed)

    return products, errors


