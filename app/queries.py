"""Read-side queries for the comparison app. Pure functions over a sqlite
connection (no HTTP), so they can be tested directly."""
import statistics
from datetime import datetime, timezone

from common.beer_types import TYPES, beer_type, display_name
from common.db import get_meta
from common.states import STATES, has_stores, normalise_state
from common.value import value_metrics

RETAILERS = ["dan_murphys", "bws", "liquorland"]
RETAILER_NAMES = {
    "dan_murphys": "Dan Murphy's",
    "bws": "BWS",
    "liquorland": "Liquorland",
}
STALE_DAYS = 7
SORTS = {"value", "unit_price", "abv", "rating", "name"}
# "Highest rated" pulls ratings with few reviews toward 4.0, so one 5-star review doesn't outrank 4.6 from 300.
# (Mirrored in app/static/staticdata.js.)
RATING_PRIOR = 4.0
RATING_PRIOR_WEIGHT = 5


def _parse(ts):
    return datetime.fromisoformat(ts)


def pack_label(pack_type, units):
    if pack_type == "single":
        return "Single"
    if pack_type == "case":
        return f"Case of {units}"
    return f"{units} pack"


# --------------------------------------------------------------------------
# Locations
# --------------------------------------------------------------------------


def resolve_locations(conn, state):
    """Pick the location each retailer's prices come from for a state/territory.

    Each location was scraped as "the store nearest the state's postcode"
    (common/states.py), so the freshest same-state location is used. There is
    NO cross-state fallback: a retailer with nothing for this state is None and
    a notice says so (showing another state's prices, or comparing retailers
    across states, would be wrong). Raises ValueError for an unknown state.
    """
    code = normalise_state(state)

    if code is None:
        raise ValueError("Choose a valid state or territory.")

    freshness = {
        row["location_key"]: row["latest"]
        for row in conn.execute(
            "SELECT location_key, MAX(observed_at) latest "
            "FROM price_observations GROUP BY location_key"
        )
    }
    rows = [
        dict(r) for r in conn.execute("SELECT * FROM locations")
        if r["location_key"] in freshness and r["state"] == code
    ]
    chosen, notices = {}, []

    for retailer in RETAILERS:
        candidates = [r for r in rows if r["retailer"] == retailer]

        if not has_stores(retailer, code):
            chosen[retailer] = None
            notices.append(f"{RETAILER_NAMES[retailer]} has no stores in {STATES[code]['name']}.")
            continue

        if not candidates:
            chosen[retailer] = None
            notices.append(f"No {RETAILER_NAMES[retailer]} prices for {STATES[code]['name']} yet.")
            continue

        pick = max(candidates, key=lambda r: freshness[r["location_key"]])
        chosen[retailer] = {
            "location_key": pick["location_key"],
            "store_name": pick["store_name"],
            "suburb": pick["suburb"],
            "state": pick["state"],
            "postcode": pick["postcode"],
            "latest": freshness[pick["location_key"]],
        }

    return {
        "state": code,
        "state_name": STATES[code]["name"],
        "postcode": STATES[code]["postcode"],   # the postcode we price each state from
        "retailers": chosen,
        "notices": notices,
    }


# --------------------------------------------------------------------------
# Comparison
# --------------------------------------------------------------------------

_LATEST_OPTIONS_SQL = """
SELECT l.id AS listing_id, l.retailer_sku, l.url, l.name AS lname,
       l.abv AS labv, l.unit_volume_ml AS lvol, l.product_id,
       l.rating AS lrating, l.review_count AS lreviews,
       p.name AS pname, p.brand AS pbrand, p.abv AS pabv,
       p.abv_source, p.unit_volume_ml AS pvol,
       o.pack_type, o.units, o.member_only, o.price, o.observed_at
FROM (
    SELECT *, ROW_NUMBER() OVER (
        PARTITION BY listing_id, pack_type, units, member_only
        ORDER BY observed_at DESC, id DESC
    ) AS rn
    FROM price_observations
    WHERE location_key = ?
) o
JOIN (
    -- the day each listing was last seen at this location
    SELECT listing_id, MAX(date(observed_at)) AS last_day
    FROM price_observations WHERE location_key = ? GROUP BY listing_id
) seen ON seen.listing_id = o.listing_id
JOIN listings l ON l.id = o.listing_id
LEFT JOIN products p ON p.id = l.product_id
-- A pack/price that was NOT re-observed on the listing's latest scrape day has
-- gone (out of stock, or a parsing rule changed): don't show it as current.
WHERE o.rn = 1 AND l.retailer = ? AND date(o.observed_at) = seen.last_day
"""


def _option(row, abv, abv_source, volume, now):
    metrics = value_metrics(row["price"], row["units"], volume, abv, abv_source)
    age_days = (now - _parse(row["observed_at"])).days

    return {
        "pack_type": row["pack_type"],
        "units": row["units"],
        "label": pack_label(row["pack_type"], row["units"]),
        "member_only": bool(row["member_only"]),
        "price": row["price"],
        "unit_price": round(row["price"] / row["units"], 2),
        "observed_at": row["observed_at"],
        "stale": age_days > STALE_DAYS,
        **{k: metrics[k] for k in (
            "standard_drinks", "price_per_standard_drink", "price_per_100ml_alcohol",
        )},
    }


def _best(options, key):
    pool = [o for o in options if o[key] is not None]
    return min(pool, key=lambda o: o[key]) if pool else None


def _finish_entry(entry):
    """Derived per-retailer fields, from the options the entry currently holds."""
    entry["options"].sort(key=lambda o: (o["units"], o["member_only"]))
    entry["best_value"] = _best(entry["options"], "price_per_standard_drink")
    entry["cheapest_unit"] = _best(entry["options"], "unit_price")
    entry["last_updated"] = max(o["observed_at"] for o in entry["options"])
    entry["stale"] = all(o["stale"] for o in entry["options"])


def load_products(conn, locations, now=None):
    """All matched products with EVERY price option each retailer has at the
    chosen locations (member offers and all pack sizes). `refilter` narrows
    them; the static site does the same narrowing in the browser."""
    now = now or datetime.now(timezone.utc)
    products = {}

    for retailer, loc in locations["retailers"].items():
        if not loc:
            continue

        for row in conn.execute(_LATEST_OPTIONS_SQL, (loc["location_key"], loc["location_key"], retailer)):
            if row["product_id"] is None:
                continue

            volume = row["pvol"] or row["lvol"]
            abv = row["pabv"] if row["pabv"] is not None else row["labv"]
            source = row["abv_source"] if row["pabv"] is not None else (retailer if abv is not None else None)

            raw = row["pname"] or row["lname"]
            product = products.setdefault(
                row["product_id"],
                {
                    "id": row["product_id"],
                    "name": display_name(raw),
                    "raw_name": raw,
                    "type": beer_type(raw, abv),
                    "unit_volume_ml": volume,
                    "abv": abv,
                    "abv_source": source,
                    "retailers": {},
                },
            )
            entry = product["retailers"].setdefault(
                retailer,
                {"url": row["url"], "sku": row["retailer_sku"], "options": [],
                 "rating": row["lrating"], "review_count": row["lreviews"]},
            )
            entry["options"].append(_option(row, abv, source, volume, now))

    for product in products.values():
        for entry in product["retailers"].values():
            _finish_entry(entry)

        _mark_best(product)
        product["rating"], product["review_count"] = combined_rating(product["retailers"].values())

    return list(products.values())


def rating_score(product):
    """What "highest rated" sorts by: the rating, nudged toward the prior when there are few reviews."""
    votes = product.get("review_count") or 1
    return (product["rating"] * votes + RATING_PRIOR * RATING_PRIOR_WEIGHT) / (votes + RATING_PRIOR_WEIGHT)


def combined_rating(entries):
    """One average across retailers, weighted by how many reviews each has
    (reviews are of the same beer, so they pool). (None, 0) if nobody has any."""
    rated = [e for e in entries if e.get("rating")]
    weights = [e.get("review_count") or 1 for e in rated]

    if not rated:
        return None, 0

    average = sum(e["rating"] * w for e, w in zip(rated, weights)) / sum(weights)
    return round(average, 2), sum(e.get("review_count") or 0 for e in rated)


def refilter(products, include_member=True, min_units=None, max_units=None):
    """Keep only the price options that are wanted (member offers or not, a range
    of pack sizes) and redo everything derived from them: best prices, who wins,
    last updated. Products with nothing left are dropped. Mirrored exactly by
    applyQuery in app/static/staticdata.js (a parity test checks it)."""
    kept = []

    for product in products:
        retailers = {}

        for retailer, entry in product["retailers"].items():
            options = [
                o for o in entry["options"]
                if (include_member or not o["member_only"])
                and (min_units is None or o["units"] >= min_units)
                and (max_units is None or o["units"] <= max_units)
            ]

            if not options:
                continue

            fresh = {**entry, "options": list(options)}
            _finish_entry(fresh)
            retailers[retailer] = fresh

        if not retailers:
            continue

        copy = {**product, "retailers": retailers}
        _mark_best(copy)
        kept.append(copy)

    return kept


def facets(products):
    """What the filter dropdowns can offer for these products."""
    units = sorted({o["units"] for p in products for e in p["retailers"].values() for o in e["options"]})
    counts = {}

    for p in products:
        counts[p["type"]] = counts.get(p["type"], 0) + 1

    return {
        "units": units,
        "types": [{"type": t, "count": counts[t]} for t in TYPES if t in counts],
    }


def _mark_best(product):
    """Flag the retailer that wins on value (and on per-unit price)."""
    for metric, field, flag in (
        ("price_per_standard_drink", "best_value", "best_value"),
        ("unit_price", "cheapest_unit", "best_unit_price"),
    ):
        scored = [
            (entry[field][metric], retailer)
            for retailer, entry in product["retailers"].items()
            if entry[field]
        ]
        winner = min(scored)[1] if len(scored) >= 2 else None
        product[flag + "_retailer"] = winner
        product["min_" + metric] = min((s for s, _ in scored), default=None)


# sort name -> (what to sort on, direction by default: 1 = low to high, -1 = high to low)
_NUMERIC_SORTS = {
    "value": (lambda p: p["min_price_per_standard_drink"], 1),
    "unit_price": (lambda p: p["min_unit_price"], 1),
    "abv": (lambda p: p["abv"], -1),
    "rating": (lambda p: rating_score(p) if p["rating"] else None, -1),
}


def ordered(products, sort, reverse=False):
    """Products in the sort's natural order, or the opposite with `reverse`. Products with no value
    for the sort (no ABV, no rating...) always come last, whichever way round. Mirrored in staticdata.js."""
    if sort == "name":
        return sorted(products, key=lambda p: p["name"].lower(), reverse=reverse)

    get, natural = _NUMERIC_SORTS[sort]
    direction = -natural if reverse else natural

    def key(p):
        value = get(p)
        return (value is None, direction * value if value is not None else 0, p["name"])

    return sorted(products, key=key)


def compare(conn, state, q="", sort="value", include_member=True, retailers=None,
            min_abv=None, max_abv=None, min_retailers=1, min_units=None, max_units=None,
            types=None, reverse=False, limit=50, offset=0, now=None):
    if sort not in SORTS:
        raise ValueError(f"sort must be one of {sorted(SORTS)}")
    unknown = set(types or []) - set(TYPES)
    if unknown:
        raise ValueError(f"unknown beer type(s): {sorted(unknown)}")

    locations = resolve_locations(conn, state)
    everything = load_products(conn, locations, now)
    available = facets(everything)
    products = refilter(everything, include_member, min_units, max_units)
    tokens = q.lower().split()
    wanted = set(retailers) if retailers else None
    wanted_types = set(types) if types else None
    result = []

    for p in products:
        if tokens and not all(t in p["raw_name"].lower() for t in tokens):
            continue
        if wanted and not wanted <= set(p["retailers"]):      # sold by EVERY picked retailer; none picked = any
            continue
        if wanted_types and p["type"] not in wanted_types:
            continue
        if len(p["retailers"]) < min_retailers:
            continue
        if (min_abv is not None or max_abv is not None):
            if p["abv"] is None:
                continue
            if min_abv is not None and p["abv"] < min_abv:
                continue
            if max_abv is not None and p["abv"] > max_abv:
                continue
        result.append(p)

    result = ordered(result, sort, reverse)

    total = len(result)
    latest = [loc["latest"] for loc in locations["retailers"].values() if loc]

    return {
        "meta": {
            "total": total,
            "limit": limit,
            "offset": offset,
            "demo": get_meta(conn, "demo") == "1",
            "latest_data": max(latest) if latest else None,
            "facets": available,
        },
        "locations": locations,
        "products": result[offset: offset + limit],
    }


# --------------------------------------------------------------------------
# Product detail + history
# --------------------------------------------------------------------------


def _signal(prices):
    """Is the current price good compared with what we've seen?"""
    distinct_days = len(prices)

    if distinct_days < 3:
        return "not_enough_history"

    current = prices[-1]
    median = statistics.median(prices)

    if current <= min(prices):
        return "lowest_seen"
    if current < median * 0.97:
        return "below_usual"
    if current > median * 1.03:
        return "above_usual"
    return "usual"


def product_detail(conn, product_id, state, now=None):
    now = now or datetime.now(timezone.utc)
    locations = resolve_locations(conn, state)
    products = [
        p for p in load_products(conn, locations, now)
        if p["id"] == product_id
    ]

    if not products:
        return None

    product = products[0]
    history = {}

    for retailer, entry in product["retailers"].items():
        loc = locations["retailers"][retailer]
        rows = conn.execute(
            """
            SELECT o.pack_type, o.units, o.member_only, o.price, o.observed_at
            FROM price_observations o JOIN listings l ON l.id = o.listing_id
            WHERE l.retailer = ? AND l.retailer_sku = ? AND o.location_key = ?
            ORDER BY o.observed_at, o.id
            """,
            (retailer, entry["sku"], loc["location_key"]),
        ).fetchall()
        series = {}

        for r in rows:
            key = f"{r['pack_type']}:{r['units']}:{int(r['member_only'])}"
            series.setdefault(key, []).append(
                {"t": r["observed_at"], "price": r["price"]}
            )

        history[retailer] = {
            key: {
                "points": points,
                "label": pack_label(*key.split(":")[:1], int(key.split(":")[1]))
                + (" (member offer)" if key.endswith(":1") else ""),
                "min": min(p["price"] for p in points),
                "max": max(p["price"] for p in points),
                "current": points[-1]["price"],
                "n": len(points),
                "first_seen": points[0]["t"],
                "signal": _signal([p["price"] for p in points]),
            }
            for key, points in series.items()
        }

    return {
        "product": product,
        "history": history,
        "locations": locations,
        "demo": get_meta(conn, "demo") == "1",
    }
