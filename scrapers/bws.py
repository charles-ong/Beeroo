"""BWS parsing (pure functions, tested against saved captures).

BWS uses an older Endeavour format than Dan Murphy's: ProductGroup
"Items", each holding one product per pack size (single / 6 / 24 ...),
with one shelf `Price` per product. ABV is in AdditionalDetails "alcohol%".
"""
import re

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

BASE_URL = "https://bws.com.au"
CASE_MIN_UNITS = 12


def _details(product):
    return {
        d.get("Name"): d.get("Value")
        for d in product.get("AdditionalDetails") or []
    }


def _clean(text):
    return re.sub(r"\s+", " ", (text or "").replace("<br>", " ")).strip()


def _units(product):
    value = _details(product).get("productunitquantity")
    try:
        return max(int(value), 1)
    except (TypeError, ValueError):
        return 1


def _pack_type(units, base_units):
    if units == 1:
        return PackType.SINGLE
    return PackType.CASE if base_units >= CASE_MIN_UNITS else PackType.PACK


def parse_item_prices(products, location_key, observed_at):
    """Online-available price options for one item's pack-size products.

    - Price: standard shelf price.
    - "AppBasedOffer" FixedPricePromo ("on app for"): per-pack app price,
      flagged member_only.
    - "N for" FixedPricePromo: total price for N packs (public multi-buy).
    Unavailable products are skipped.
    """
    observations = []

    for product in products:
        if not product.get("IsAvailable"):
            continue

        units = _units(product)
        price = product.get("Price")

        if price:
            observations.append(
                PriceObservation(
                    pack_type=_pack_type(units, units),
                    units=units,
                    price=float(price),
                    location_key=location_key,
                    observed_at=observed_at,
                )
            )

        tag = product.get("FixedPricePromoTag") or {}
        promo = tag.get("PromotionalPrice") or 0

        if not promo:
            continue

        multiplier = tag.get("ProductMultiplier") or 0

        if product.get("PromotionType") == "AppBasedOffer":
            observations.append(
                PriceObservation(
                    pack_type=_pack_type(units, units),
                    units=units,
                    price=float(promo),
                    member_only=True,
                    location_key=location_key,
                    observed_at=observed_at,
                )
            )
        elif multiplier > 1:
            total_units = multiplier * units
            observations.append(
                PriceObservation(
                    pack_type=_pack_type(total_units, units),
                    units=total_units,
                    price=float(promo),
                    location_key=location_key,
                    observed_at=observed_at,
                )
            )

    unique = {}

    for obs in observations:
        key = (obs.pack_type, obs.units, obs.price, obs.member_only)
        unique.setdefault(key, obs)

    return list(unique.values())


def parse_item(item, location_key, observed_at):
    products = item.get("Products") or []

    if not products:
        raise ValueError("item has no products")

    parent = str(item["PackParentStockCode"])
    base = next((p for p in products if _units(p) == 1), products[0])
    details = _details(base)

    abv = parse_abv(str(details.get("alcohol%") or ""))
    volume = parse_volume_ml(
        str(details.get("liquorsize") or base.get("PackageSize") or "")
    )

    listing = Listing(
        retailer=Retailer.BWS,
        retailer_sku=parent,
        url=f"{BASE_URL}/product/{parent}/{base.get('UrlFriendlyName') or ''}".rstrip("/"),
        name=_clean(base.get("Name")) or _clean(item.get("Name")),
        brand=_clean(base.get("BrandName") or details.get("brand_name")) or None,
        category="beer",
        abv=abv,
        unit_volume_ml=volume,
    )

    return ScrapedProduct(
        listing=listing,
        prices=parse_item_prices(products, location_key, observed_at),
    )


def parse_bws_payload(payload, location_key, observed_at=None):
    """Parse a ProductGroup/Products response. Returns (products, errors)."""
    observed_at = observed_at or utcnow()
    products, errors = [], []

    for item in payload.get("Items") or []:
        sku = item.get("PackParentStockCode")

        try:
            parsed = parse_item(item, location_key, observed_at)
        except (ValueError, KeyError) as e:
            errors.append((sku, str(e)))
            continue

        if not parsed.prices:
            errors.append((sku, "no available online prices"))
            continue

        products.append(parsed)

    return products, errors


def location_from_set_pickup(payload):
    """Location from an Address/SetPickupByStoreNo response."""
    details = (
        (payload or {}).get("FulfilmentInfo", {}).get("ClickAndCollectDetails")
        or {}
    )
    store_id = details.get("FulfilmentStoreID")

    if not store_id:
        return None

    return Location(
        retailer=Retailer.BWS,
        store_id=str(store_id),
        store_name=details.get("FulfilmentStoreName"),
        suburb=details.get("AddressSuburb"),
        state=details.get("AddressState"),
        postcode=details.get("AddressPostalCode"),
    )
