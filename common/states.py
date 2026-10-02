"""States and territories, and the postcode each one is priced from.

Every price in the app is "the store nearest this postcode" for the chosen
state or territory. NSW uses 2100 (Brookvale / Allambie Heights); the others
use their capital city's CBD postcode. Change a value here and the scrapers,
exporter and UI all follow.
"""

STATES = {
    "NSW": {"name": "New South Wales", "postcode": "2100"},
    "ACT": {"name": "Australian Capital Territory", "postcode": "2600"},
    "VIC": {"name": "Victoria", "postcode": "3000"},
    "QLD": {"name": "Queensland", "postcode": "4000"},
    "SA": {"name": "South Australia", "postcode": "5000"},
    "WA": {"name": "Western Australia", "postcode": "6000"},
    "TAS": {"name": "Tasmania", "postcode": "7000"},
    "NT": {"name": "Northern Territory", "postcode": "0800"},
}


def state_list():
    """[{code, name, postcode}] in display order (by name)."""
    return sorted(
        ({"code": code, **info} for code, info in STATES.items()),
        key=lambda s: s["name"],
    )


def normalise_state(value):
    """'nsw ' -> 'NSW'; None if it isn't a known state/territory code."""
    code = str(value or "").strip().upper()
    return code if code in STATES else None


# Retailers that have no stores at all in a state/territory, so "no prices yet" would be wrong.
NO_STORES = {("dan_murphys", "NT")}


def has_stores(retailer, code):
    return (retailer, code) not in NO_STORES
