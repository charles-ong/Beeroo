"""Volume / ABV parsing from product names and standard-drink maths.

Australian standard drink = 10 g alcohol; ethanol density 0.789 g/mL,
so standard_drinks = litres x ABV% x 0.789.
"""
import re

ETHANOL_DENSITY = 0.789

_VOLUME_RE = re.compile(r"(\d+(?:\.\d+)?)\s*(mL|ml|L|l)\b")
_MULTI_VOLUME_RE = re.compile(
    r"\b(\d+)\s*x\s*(\d+(?:\.\d+)?)\s*(mL|ml|L)\b", re.IGNORECASE
)
_ABV_RE = re.compile(r"(\d+(?:\.\d+)?)\s*%")


def parse_volume_ml(name):
    """Per-unit volume in mL from e.g. '355mL', '1.25L', '24 x 375mL'."""
    if not name:
        return None

    match = _MULTI_VOLUME_RE.search(name) or _VOLUME_RE.search(name)
    if not match:
        return None

    value = float(match.group(match.lastindex - 1))
    unit = match.group(match.lastindex).lower()
    ml = value * 1000 if unit == "l" else value

    return ml if ml > 0 else None


def parse_abv(name):
    """ABV percentage if the name states one, else None."""
    if not name:
        return None

    match = _ABV_RE.search(name)
    if not match:
        return None

    abv = float(match.group(1))
    return abv if 0 <= abv <= 100 else None


def standard_drinks(volume_ml, abv):
    if volume_ml is None or abv is None:
        return None
    return volume_ml / 1000 * abv * ETHANOL_DENSITY


def price_per_standard_drink(price, units, unit_volume_ml, abv):
    drinks = standard_drinks(unit_volume_ml, abv)
    if drinks is None or drinks <= 0 or not units:
        return None
    return price / (drinks * units)
