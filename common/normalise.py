"""Convert the legacy scraper dicts into validated records."""
import re
from urllib.parse import urlsplit

from common.records import (
    Listing,
    PackType,
    PriceObservation,
    Retailer,
    ScrapedProduct,
)

_SKU_RE = re.compile(r"/product/(?:DM_)?(\w+)/", re.IGNORECASE)

_UNIT_TO_PACK_TYPE = {
    "each": PackType.SINGLE,
    "single": PackType.SINGLE,
    "bottle": PackType.SINGLE,
    "pack": PackType.PACK,
    "block": PackType.PACK,
    "bottles": PackType.PACK,
    "case": PackType.CASE,
    "cases": PackType.CASE,
    "carton": PackType.CASE,
}


def sku_from_url(url):
    """'/product/DM_587292/...' and '/product/587292/...' -> '587292'."""
    match = _SKU_RE.search(urlsplit(url).path)
    return match.group(1) if match else None


def pack_type_for(unit, quantity):
    pack_type = _UNIT_TO_PACK_TYPE.get((unit or "").lower())
    if pack_type is None:
        return None
    if pack_type is PackType.SINGLE and (quantity or 1) > 1:
        return PackType.PACK
    return pack_type


def legacy_to_scraped_products(rows, retailer=Retailer.DAN_MURPHYS):
    """Group legacy rows (one per price option) by listing; skip invalid rows.

    Returns (products, errors) where errors are (row, reason) tuples so
    schema drift is visible instead of silently dropped.
    """
    grouped = {}
    errors = []

    for row in rows:
        try:
            sku = sku_from_url(row["url"])
            if sku is None:
                raise ValueError("no SKU in url")

            pack_type = pack_type_for(row.get("unit"), row.get("quantity"))
            if pack_type is None:
                raise ValueError(f"unknown unit {row.get('unit')!r}")

            listing = Listing(
                retailer=retailer,
                retailer_sku=sku,
                url=row["url"],
                name=row["name"],
            )
            price = PriceObservation(
                pack_type=pack_type,
                units=row.get("quantity") or 1,
                price=row["price"],
            )
        except (ValueError, KeyError) as e:
            errors.append((row, str(e)))
            continue

        product = grouped.setdefault(
            sku, ScrapedProduct(listing=listing, prices=[])
        )
        key = (price.pack_type, price.units, price.price, price.member_only)
        if key not in {
            (p.pack_type, p.units, p.price, p.member_only)
            for p in product.prices
        }:
            product.prices.append(price)

    return list(grouped.values()), errors
