"""Value-for-alcohol metrics with explicit handling of unknown ABV.

Policy: a standard-drink figure is only shown when ABV is known, either from
the retailer's own data or borrowed from the same product at another
retailer (abv_source says which). Otherwise status is "abv_unknown" and no
number is invented.
"""
from common.units import standard_drinks


def value_metrics(price, units, unit_volume_ml, abv, abv_source=None):
    base = {
        "status": "ok",
        "abv": abv,
        "abv_source": abv_source,
        "standard_drinks": None,
        "price_per_standard_drink": None,
        "price_per_100ml_alcohol": None,
    }

    if abv is None or not unit_volume_ml:
        return {**base, "status": "abv_unknown"}

    if abv <= 0:
        return {**base, "status": "zero_alcohol"}

    per_unit = standard_drinks(unit_volume_ml, abv)
    total = per_unit * units
    alcohol_ml = unit_volume_ml * units * abv / 100

    return {
        **base,
        "standard_drinks": round(total, 2),
        "price_per_standard_drink": round(price / total, 3),
        "price_per_100ml_alcohol": round(price / alcohol_ml * 100, 2),
    }
