import re


def parse_price(line):
    match = re.search(
        r"\$(\d+(?:\.\d{1,2})?)\s+for\s+(\d+)\s+([A-Za-z]+)",
        line,
        re.IGNORECASE,
    )
    if match:
        return {
            "price": float(match.group(1)),
            "quantity": int(match.group(2)),
            "unit": match.group(3).lower(),
            "is_multi_pack": True,
        }

    match = re.search(
        r"\$(\d+(?:\.\d{1,2})?)\s+([A-Za-z]+)\s*\((\d+)\)",
        line,
        re.IGNORECASE,
    )
    if match:
        return {
            "price": float(match.group(1)),
            "quantity": int(match.group(3)),
            "unit": match.group(2).lower(),
            "is_multi_pack": False,
        }

    match = re.search(
        r"\$(\d+(?:\.\d{1,2})?)\s+([A-Za-z]+)\b",
        line,
        re.IGNORECASE,
    )
    if match:
        return {
            "price": float(match.group(1)),
            "quantity": None,
            "unit": match.group(2).lower(),
            "is_multi_pack": False,
        }

    match = re.search(r"\$(\d+(?:\.\d{1,2})?)", line)
    if match:
        return {
            "price": float(match.group(1)),
            "quantity": None,
            "unit": None,
            "is_multi_pack": False,
        }

    return None


_MULTI_BUY_UNITS = {
    "pack": {"pack", "packs"},
    "case": {"case", "cases"},
}


def resolve_multi_pack_prices(price_options):
    """Turn '$X for N packs/cases' into a total unit count (N x pack size)."""
    sizes = {}

    for option in price_options:
        if option.get("is_multi_pack") or option.get("quantity") is None:
            continue

        unit = (option.get("unit") or "").lower()

        for canonical, aliases in _MULTI_BUY_UNITS.items():
            if unit in aliases and canonical not in sizes:
                sizes[canonical] = option["quantity"]

    resolved = []

    for option in price_options:
        quantity = option["quantity"]
        unit = option["unit"]

        if option.get("is_multi_pack"):
            for canonical, aliases in _MULTI_BUY_UNITS.items():
                if (
                    (unit or "").lower() in aliases
                    and canonical in sizes
                ):
                    quantity = option["quantity"] * sizes[canonical]
                    unit = canonical
                    break

        resolved.append({
            "price": option["price"],
            "quantity": quantity,
            "unit": unit,
        })

    return resolved


def extract_title_quantity(name):
    match = re.search(
        r"\b(\d+)\s*x\s*\d+(?:\.\d+)?\s*mL\b",
        name,
        re.IGNORECASE,
    )
    if match:
        return int(match.group(1))

    match = re.search(r"\b(\d+)\s*x\b", name, re.IGNORECASE)
    if match:
        return int(match.group(1))

    return None


def extract_product_name(lines):
    cleaned = []

    for line in lines:
        line = line.strip()

        if not line:
            continue

        if re.search(r"\(\s*\d*\s*REVIEWS?\s*\)", line, re.IGNORECASE):
            continue

        if re.search(r"\bREVIEWS?\b", line, re.IGNORECASE):
            continue

        if line.upper() in {
            "MEMBER OFFER",
            "NON-MEMBER",
            "ADD TO CART",
            "SPONSORED",
        }:
            continue

        # review-count fragments such as "(116" or ")"
        if re.fullmatch(r"[()\s\d]*", line):
            continue

        if "$" in line:
            continue

        cleaned.append(line)

    if len(cleaned) >= 2:
        return f"{cleaned[0]} {cleaned[1]}".strip()

    if cleaned:
        return cleaned[0]

    return None


def normalise_price_option(option, product_name):
    if (
        option["quantity"] is None
        and (option.get("unit") or "").lower() == "each"
    ):
        title_quantity = extract_title_quantity(product_name)

        return {
            **option,
            "quantity": title_quantity if title_quantity is not None else 1,
        }

    return option


_BARE_PRICE_RE = re.compile(r"\$\d+(?:\.\d{1,2})?")
_UNIT_LINE_RE = re.compile(
    r"(?:[A-Za-z]+\s*\((?:\d+|[A-Za-z][A-Za-z\- ]*)\)"
    r"|for\s+\d+\s+[A-Za-z]+"
    r"|each|case|cases|pack|packs|block|bottles|single)",
    re.IGNORECASE,
)


def merge_split_price_lines(lines):
    """Rejoin '$71.99' + 'case (24)' when markup put them on separate lines."""
    merged = []
    i = 0

    while i < len(lines):
        line = lines[i]

        if (
            _BARE_PRICE_RE.fullmatch(line)
            and i + 1 < len(lines)
            and _UNIT_LINE_RE.fullmatch(lines[i + 1])
        ):
            merged.append(f"{line} {lines[i + 1]}")
            i += 2
            continue

        merged.append(line)
        i += 1

    return merged


def extract_price_options(lines, product_name):
    member_prices = []
    non_member_prices = []
    normal_prices = []

    member_offer_found = False
    non_member_section = False

    for line in lines:
        upper = line.upper()

        if "MEMBER OFFER" in upper:
            member_offer_found = True
            non_member_section = False
            continue

        if "NON-MEMBER" in upper:
            non_member_section = True
            parsed = parse_price(line)
            if parsed:
                non_member_prices.append(parsed)
            continue

        if "$" not in line:
            continue

        parsed = parse_price(line)
        if not parsed:
            continue

        if member_offer_found and not non_member_section:
            member_prices.append(parsed)
        elif not member_offer_found:
            normal_prices.append(parsed)

    if member_prices:
        price_options = list(member_prices)

        member_keys = {
            (
                price["quantity"],
                price["unit"].lower() if price["unit"] else None,
            )
            for price in member_prices
            if not price.get("is_multi_pack")
        }

        for price in non_member_prices:
            if price.get("is_multi_pack"):
                price_options.append(price)
                continue

            key = (
                price["quantity"],
                price["unit"].lower() if price["unit"] else None,
            )

            if key not in member_keys:
                price_options.append(price)
    else:
        price_options = normal_prices + non_member_prices

    price_options = resolve_multi_pack_prices(price_options)

    return [
        normalise_price_option(option, product_name)
        for option in price_options
    ]
