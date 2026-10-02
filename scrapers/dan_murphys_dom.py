"""Dan Murphy's product cards, read from the rendered category page.

This is the approach that worked from a home connection before: read what a
visitor sees on each `shop-product-card` (name, "$71.99 case (24)", "MEMBER
OFFER $42 for 2 packs / Non-Member: $25.99 pack (6)"). The browser driver
(scrapers/dan_murphys.py) uses it when the page's own JSON isn't captured, and
sends the raw card text to the server as kind `dan_murphys_cards`.

Pure functions only: no browser, no network. Handles the card text both as
`inner_text()` gives it ("$71.99 case (24)") and as a DOM text dump gives it
("$71.99", "case (24)" on separate lines).
"""
import re

from common.records import (
    Listing,
    PackType,
    PriceObservation,
    Retailer,
    ScrapedProduct,
    pack_type_for_units,
    utcnow,
)

DM_BASE_URL = "https://www.danmurphys.com.au"
MAX_CARDS = 3000
MAX_LINES = 60
MAX_LINE_CHARS = 300

_PRODUCT_HREF = re.compile(r"/product/(?:DM_)?(\d+)/([A-Za-z0-9%._~-]*)")
_UNIT = r"cases?|packs?|each|bottles?|cans?|blocks?|cartons?"
_TOKEN = re.compile(
    rf"""
      (?P<member>MEMBER\s+OFFER)
    | (?P<nonmember>NON-MEMBER\s*:?)
    | \$\s*(?P<price>\d+(?:\s*\.\s*\d{{1,2}})?)\s*
      (?: for\s+(?P<mq>\d+)\s+(?P<mu>cases?|packs?|bottles?|cans?|blocks?|cartons?)
        | (?P<unit>{_UNIT})(?:\s*\(+\s*(?P<paren>\d+|in-store)\s*\)+)? )?
    """,
    re.I | re.X,
)
_SKIP_LINES = {"MEMBER OFFER", "NON-MEMBER", "ADD TO CART", "SPONSORED", ")"}
_TITLE_QTY = re.compile(r"\b(\d+)\s*x\s*\d+(?:\.\d+)?\s*mL\b", re.I)
_PER_WORD = {"case": "case", "block": "case", "carton": "case", "pack": "pack", "bottle": "one", "can": "one"}


def _clean_lines(lines):
    return [re.sub(r"\s+", " ", str(l)).strip() for l in lines if str(l).strip()]


def product_name(lines):
    """(brand, name): the first two lines that aren't reviews, prices or buttons."""
    kept = []
    for line in lines:
        if re.fullmatch(r"\(?\s*\d*\s*", line) or re.search(r"\bREVIEWS?\b", line, re.I):
            continue
        if line.upper() in _SKIP_LINES or re.search(r"\$\s*\d", line) or line.upper().startswith(("MEMBER", "NON-MEMBER")):
            continue
        kept.append(line)
        if len(kept) == 2:
            break
    if len(kept) == 2:
        return kept[0], f"{kept[0]} {kept[1]}"
    return (None, kept[0]) if kept else (None, None)


def _options(text):
    """Every price phrase in order, tagged member / non-member."""
    section, found = None, []
    for m in _TOKEN.finditer(text):
        if m.group("member"):
            section = "member"
        elif m.group("nonmember"):
            section = "non_member"
        else:
            found.append({
                "price": float(re.sub(r"\s+", "", m.group("price"))),
                "mq": int(m.group("mq")) if m.group("mq") else None,
                "mu": (m.group("mu") or "").lower().rstrip("s"),
                "unit": (m.group("unit") or "").lower().rstrip("s"),
                "paren": m.group("paren"),
                "member": section == "member",
            })
    return found


def _sibling_size(options, unit):
    for o in options:
        if o["mq"] is None and o["unit"] == unit and (o["paren"] or "").isdigit():
            return int(o["paren"])
    return None


def parse_prices(lines, name, location_key, observed_at):
    """Online-purchasable price options for one card. An option we can't turn
    into a unit count is skipped, never guessed; if that leaves nothing, raises
    ValueError saying why."""
    options = _options(" ".join(lines))
    title = _TITLE_QTY.search(name or "")
    seen, observations, problem = set(), [], None

    for o in options:
        if o["mq"]:                                     # "$42 for 2 packs"
            kind = _PER_WORD[o["mu"]]
            per = 1 if kind == "one" else _sibling_size(options, "case" if kind == "case" else "pack")
            if not per:
                problem = problem or f"cannot size multi-buy '{o['mq']} {o['mu']}'"
                continue
            units = o["mq"] * per
            pack_type = PackType.CASE if kind == "case" else pack_type_for_units(units)
        elif o["paren"] == "in-store":                  # not purchasable online
            continue
        elif (o["paren"] or "").isdigit():              # "$71.99 case (24)"
            units = int(o["paren"])
            pack_type = pack_type_for_units(units)
        elif o["unit"] == "each":                       # "$19.99 each"
            units = int(title.group(1)) if title else 1
            pack_type = pack_type_for_units(units)
        else:
            problem = problem or "price without a unit count"
            continue

        key = (pack_type, units, o["member"])
        if key in seen:
            continue
        seen.add(key)
        observations.append(PriceObservation(
            pack_type=pack_type, units=units, price=o["price"], member_only=o["member"],
            location_key=location_key, observed_at=observed_at,
        ))

    if not observations and problem:
        raise ValueError(problem)
    return observations


def parse_card(card, location_key, observed_at=None):
    """One {"href": ..., "lines": [...]} card -> ScrapedProduct. Raises ValueError."""
    observed_at = observed_at or utcnow()
    link = _PRODUCT_HREF.search(str(card.get("href") or ""))
    if not link:
        raise ValueError("no product link")
    sku, slug = link.group(1), link.group(2)

    lines = _clean_lines(card.get("lines") or [])
    brand, name = product_name(lines)
    if not name:
        raise ValueError("no product name")

    prices = parse_prices(lines, name, location_key, observed_at)
    if not prices:
        raise ValueError("no online prices")

    listing = Listing(
        retailer=Retailer.DAN_MURPHYS,
        retailer_sku=sku,
        url=f"{DM_BASE_URL}/product/{sku}/{slug}".rstrip("/"),
        name=name,
        brand=brand,
        category="beer",
    )
    return ScrapedProduct(listing=listing, prices=prices)


def parse_cards_payload(payload, location_key, observed_at=None):
    """Parse a {"cards": [...]} payload. Returns (products, errors) like the
    other parsers; a sku seen twice (e.g. in a carousel and the list) keeps its first card."""
    observed_at = observed_at or utcnow()
    products, errors, seen = [], [], set()
    cards = payload.get("cards")
    if not isinstance(cards, list) or len(cards) > MAX_CARDS:
        raise ValueError("payload has no usable 'cards' list")

    for card in cards:
        ident = (card.get("href") or "?")[:80] if isinstance(card, dict) else "?"
        try:
            if not isinstance(card, dict) or len(card.get("lines") or []) > MAX_LINES:
                raise ValueError("malformed card")
            parsed = parse_card(
                {"href": card.get("href"), "lines": [str(l)[:MAX_LINE_CHARS] for l in card["lines"]]},
                location_key, observed_at,
            )
        except (ValueError, KeyError) as e:
            errors.append((ident, str(e)))
            continue
        if parsed.listing.retailer_sku in seen:
            continue
        seen.add(parsed.listing.retailer_sku)
        products.append(parsed)

    return products, errors


def count_unique(cards):
    """Distinct products among the cards (the list and a carousel can repeat one)."""
    return len({m.group(1) for c in cards if (m := _PRODUCT_HREF.search(str(c.get("href") or "")))})
