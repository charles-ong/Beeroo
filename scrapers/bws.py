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


def _name_volume(product):
    """Per-unit volume as the product NAME states it ("... Cans 375ml")."""
    return parse_volume_ml(_clean(product.get("Name")))


_PACK_IN_NAME = re.compile(r"\b(\d{1,3})\s*pack\b", re.IGNORECASE)
_COUNT_X_VOLUME = re.compile(r"\b(\d{1,3})\s*x\s*\d+(?:\.\d+)?\s*(?:ml|l)\b", re.IGNORECASE)
MAX_UNITS = 60


def _units(product):
    """Units in this product, or None when it can't be determined safely.

    `productunitquantity` is reliable for the standard single / pack / case
    products. Exceptions seen in real data (quantity "1"):
      * sold as "N Pack": the name states N ("Magners ... 10 Pack Cans 330ml").
      * a CASE ("webpacktype": "Case") with no count in the name: the count is
        liquorsize / per-can volume (30 x 375 mL = 11250 mL) *if* that is a clean
        whole number; otherwise we don't know, so the price is skipped.

    NOT used otherwise: `liquorsize`. Across an item's products it can hold the
    largest pack's volume (6000ML on the single can, the 4-pack and the 16-case
    alike), so for ordinary products it says nothing.
    """
    details = _details(product)
    try:
        units = max(int(details.get("productunitquantity")), 1)
    except (TypeError, ValueError):
        units = 1

    name = _clean(product.get("Name"))

    # "...Cans 10x375ml": the product's unit is a 10-pack, so quantity 1 = 10 cans
    # and quantity 3 = three 10-packs = 30 cans (verified on 8 real products).
    count = _COUNT_X_VOLUME.search(name)
    if count and int(count.group(1)) >= 2:
        total = int(count.group(1)) * units
        return total if total <= MAX_UNITS else None

    if units > 1:
        return units

    match = _PACK_IN_NAME.search(name)
    if match and int(match.group(1)) >= 2:
        return int(match.group(1))

    if str(details.get("webpacktype") or "").lower() in ("case", "carton"):
        unit_ml = _name_volume(product)
        size_ml = parse_volume_ml(str(details.get("liquorsize") or ""))
        if unit_ml and size_ml:
            ratio = size_ml / unit_ml
            if ratio >= 4 and abs(ratio - round(ratio)) < 0.02:
                return int(round(ratio))
        return None   # a case of unknown size: don't guess

    return 1


def _pack_type(units, base_units):
    if units == 1:
        return PackType.SINGLE
    return PackType.CASE if base_units >= CASE_MIN_UNITS else PackType.PACK


def parse_item_prices(products, location_key, observed_at):
    """Online-available price options for one item's pack-size products.

    - Price: standard shelf price.
    - "AppBasedOffer" FixedPricePromo ("on app for"): per-pack app price,
      flagged member_only. Ignored unless it is actually cheaper.
    - "N for" FixedPricePromo: total price for N packs (public multi-buy).
      BWS sometimes stamps a carton deal onto the single can too ("2 for $120"
      on a $5.50 can), so a multi-buy is only kept if it beats buying the
      packs separately.
    Unavailable products, and products whose unit count is unknown, are skipped.
    """
    observations = []

    for product in products:
        if not product.get("IsAvailable"):
            continue

        units = _units(product)
        if units is None:
            continue

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
            if price and promo >= price:
                continue  # not actually an offer
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
            if price and promo >= multiplier * price:
                continue  # no saving: the tag belongs to a different pack
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
    # The name states the per-unit volume; liquorsize is unreliable (see _units).
    volume = _name_volume(base) or parse_volume_ml(
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


_KEEP_PRODUCT = ("Stockcode", "Price", "Name", "UrlFriendlyName", "IsAvailable", "PackageSize", "BrandName", "PromotionType")
_KEEP_DETAILS = {"productunitquantity", "alcohol%", "liquorsize", "brand_name", "webpacktype"}


def minimal_payload(payload, only_available=True):
    """The smallest payload the parser needs: in-stock items only (a store lists
    ~745 beers but stocks ~200), allowlisted fields only. A full capture is
    ~12 MB; this is well under 1 MB and parses identically."""
    items = []
    for item in payload.get("Items") or []:
        products = item.get("Products") or []
        if only_available and not any(p.get("IsAvailable") for p in products):
            continue
        items.append({
            "PackParentStockCode": item.get("PackParentStockCode"),
            "Name": item.get("Name"),
            "Products": [
                {
                    **{k: p[k] for k in _KEEP_PRODUCT if k in p},
                    "FixedPricePromoTag": {
                        k: (p.get("FixedPricePromoTag") or {}).get(k)
                        for k in ("PromotionalPrice", "ProductMultiplier")
                    },
                    "AdditionalDetails": [
                        {"Name": d["Name"], "Value": d.get("Value")}
                        for d in p.get("AdditionalDetails") or []
                        if d.get("Name") in _KEEP_DETAILS
                    ],
                }
                for p in products
            ],
        })
    return {"TotalRecordCount": payload.get("TotalRecordCount"), "Items": items}
