"""BWS parsing (pure functions, tested against saved captures).

BWS uses an older Endeavour format than Dan Murphy's: ProductGroup
"Items", each holding one product per pack size (single / 6 / 24 ...),
with one shelf `Price` per product. ABV is in AdditionalDetails "alcohol%".
"""
import re

from common.records import (
    clean_rating,
    pack_type_for_units,
    Listing,
    Location,
    PackType,
    PriceObservation,
    Retailer,
    ScrapedProduct,
    utcnow,
)
from common.postcodes import state_for_postcode
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


def _quantity(product):
    try:
        return max(int(_details(product).get("productunitquantity")), 1)
    except (TypeError, ValueError):
        return 1


def _pack_size_in_name(name):
    """N from "...10 Pack Cans..." or "...Cans 10x375ml", else None."""
    for pattern in (_COUNT_X_VOLUME, _PACK_IN_NAME):
        match = pattern.search(name)
        if match and int(match.group(1)) >= 2:
            return int(match.group(1))
    return None


def _units(product, siblings=()):
    """Units in this product, or None when it can't be determined safely.

    `productunitquantity` (qty) is what BWS provides, but for items whose NAME
    says "N Pack" / "Nx375ml" it means different things in different items, so
    the item's other products decide (all verified on real data):

      * A sibling with qty == N is the explicit N-pack, so a qty-1 product is a
        genuine single can: Kopparberg Hard Apple Cider "10 Pack": qty 1 $5,
        qty 10 $25, qty 20 $49  ->  1, 10, 20 units.
      * No such sibling: qty 1 IS the N-pack (Magners 10 Pack: only product,
        qty 1, $32).
      * qty < N can't be a count of cans in an N-pack, so it counts packs
        (Mercury 10 Pack: qty 1 $31, qty 3 $98 -> 10 and 30 units).
      * qty >= N is already a can count (use as is).

    For a CASE (`webpacktype`) with qty 1 and no N in the name, the count is
    liquorsize / per-can volume if that is a clean whole number, else unknown
    (skipped, not guessed). `liquorsize` is otherwise NOT used: across an item's
    products it can hold the largest pack's volume.
    """
    qty = _quantity(product)
    name = _clean(product.get("Name"))
    n = _pack_size_in_name(name)

    if n:
        if qty == 1:
            others = {_quantity(p) for p in siblings if p is not product}
            return 1 if n in others else n
        if qty < n:
            total = qty * n
            return total if total <= MAX_UNITS else None
        return qty

    if qty > 1:
        return qty

    if str(_details(product).get("webpacktype") or "").lower() in ("case", "carton"):
        unit_ml = _name_volume(product)
        size_ml = parse_volume_ml(str(_details(product).get("liquorsize") or ""))
        if unit_ml and size_ml:
            ratio = size_ml / unit_ml
            if ratio >= 4 and abs(ratio - round(ratio)) < 0.02:
                return int(round(ratio))
        return None   # a case of unknown size: don't guess

    return 1


def _pack_type(units, base_units=None):
    return pack_type_for_units(units)


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

        units = _units(product, products)
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
    base = next((p for p in products if _units(p, products) == 1), products[0])
    details = _details(base)

    abv = parse_abv(str(details.get("alcohol%") or ""))
    # The name states the per-unit volume; liquorsize is unreliable (see _units).
    volume = _name_volume(base) or parse_volume_ml(
        str(details.get("liquorsize") or base.get("PackageSize") or "")
    )

    # the pack variants of one item share their reviews; take the most-reviewed figure
    rated = max(products, key=lambda p: p.get("NumberOfReviews") or 0)
    rating, review_count = clean_rating(rated.get("OverallRating"), rated.get("NumberOfReviews"))

    listing = Listing(
        retailer=Retailer.BWS,
        retailer_sku=parent,
        url=f"{BASE_URL}/product/{parent}/{base.get('UrlFriendlyName') or ''}".rstrip("/"),
        name=_clean(base.get("Name")) or _clean(item.get("Name")),
        brand=_clean(base.get("BrandName") or details.get("brand_name")) or None,
        category="beer",
        abv=abv,
        unit_volume_ml=volume,
        rating=rating,
        review_count=review_count,
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

    postcode = details.get("AddressPostalCode")
    # BWS's own state field can be wrong ("Melbourne 3000" came back as SA); the
    # postcode is authoritative, so derive the state from it when it is valid.
    state = state_for_postcode(postcode) or details.get("AddressState")

    return Location(
        retailer=Retailer.BWS,
        store_id=str(store_id),
        store_name=details.get("FulfilmentStoreName"),
        suburb=details.get("AddressSuburb"),
        state=state,
        postcode=postcode,
    )


_KEEP_PRODUCT = ("Stockcode", "Price", "Name", "UrlFriendlyName", "IsAvailable", "PackageSize", "BrandName", "PromotionType",
                 "OverallRating", "NumberOfReviews")
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
