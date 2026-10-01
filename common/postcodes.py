"""Australian postcode -> state (Australia Post ranges)."""

_RANGES = [
    (200, 299, "ACT"),
    (800, 999, "NT"),
    (1000, 2599, "NSW"),
    (2600, 2618, "ACT"),
    (2619, 2898, "NSW"),
    (2899, 2899, "NSW"),  # Norfolk Island
    (2900, 2920, "ACT"),
    (2921, 2999, "NSW"),
    (3000, 3999, "VIC"),
    (4000, 4999, "QLD"),
    (5000, 5999, "SA"),
    (6000, 6797, "WA"),
    (6800, 6999, "WA"),
    (7000, 7999, "TAS"),
    (8000, 8999, "VIC"),
    (9000, 9999, "QLD"),
]


def state_for_postcode(postcode):
    """Return the state/territory code for a 4-digit postcode, or None."""
    text = str(postcode).strip()

    if not (text.isdigit() and len(text) == 4):
        return None

    value = int(text)

    for low, high, state in _RANGES:
        if low <= value <= high:
            return state

    return None
