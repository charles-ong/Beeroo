"""Read-side queries for the comparison app. Pure functions over a sqlite
connection (no HTTP), so they can be tested directly."""
import statistics
from datetime import datetime, timezone

from common.db import get_meta
from common.postcodes import state_for_postcode
from common.value import value_metrics

RETAILERS = ["dan_murphys", "bws", "liquorland"]
RETAILER_NAMES = {
    "dan_murphys": "Dan Murphy's",
    "bws": "BWS",
    "liquorland": "Liquorland",
}
STALE_DAYS = 7
SORTS = {"value", "unit_price", "abv", "name"}
PACK_TYPES = {"single", "pack", "case"}


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


def resolve_locations(conn, postcode):
    """Pick the location each retailer's prices come from for a postcode.

    Prefers a same-state location (exact postcode first, then most recent
    data); otherwise falls back to the retailer's other location, flagged.
    Raises ValueError for an invalid postcode.
    """
    state = state_for_postcode(postcode)

    if state is None:
        raise ValueError("Enter a valid 4-digit Australian postcode.")

    freshness = {
        row["location_key"]: row["latest"]
        for row in conn.execute(
            "SELECT location_key, MAX(observed_at) latest "
            "FROM price_observations GROUP BY location_key"
        )
    }
    rows = [
        dict(r) for r in conn.execute("SELECT * FROM locations")
        if r["location_key"] in freshness
    ]
    chosen, notices = {}, []

    for retailer in RETAILERS:
        name = RETAILER_NAMES[retailer]
        candidates = [r for r in rows if r["retailer"] == retailer]

        if not candidates:
            chosen[retailer] = None
            notices.append(f"No {name} prices loaded yet.")
            continue

        same_state = [r for r in candidates if r["state"] == state]
        pool = same_state or candidates
        pool.sort(key=lambda r: freshness[r["location_key"]], reverse=True)
        pick = next((r for r in pool if r["postcode"] == postcode), pool[0])
        fallback = not same_state

        chosen[retailer] = {
            "location_key": pick["location_key"],
            "store_name": pick["store_name"],
            "suburb": pick["suburb"],
            "state": pick["state"],
            "postcode": pick["postcode"],
            "fallback": fallback,
            "latest": freshness[pick["location_key"]],
        }

        if fallback:
            notices.append(
                f"{name} prices for {state} aren't loaded yet; showing "
                f"{pick['state']} prices instead (they can differ by state)."
            )

    return {"postcode": postcode, "state": state, "retailers": chosen, "notices": notices}


# --------------------------------------------------------------------------
# Comparison
# --------------------------------------------------------------------------

_LATEST_OPTIONS_SQL = """
SELECT l.id AS listing_id, l.retailer_sku, l.url, l.name AS lname,
       l.abv AS labv, l.unit_volume_ml AS lvol, l.product_id,
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
JOIN listings l ON l.id = o.listing_id
LEFT JOIN products p ON p.id = l.product_id
WHERE o.rn = 1 AND l.retailer = ?
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


def load_products(conn, locations, include_member=False, pack_type=None, now=None):
    """All matched products with per-retailer options at the chosen locations."""
    now = now or datetime.now(timezone.utc)
    products = {}

    for retailer, loc in locations["retailers"].items():
        if not loc:
            continue

        for row in conn.execute(_LATEST_OPTIONS_SQL, (loc["location_key"], retailer)):
            if row["product_id"] is None:
                continue
            if row["member_only"] and not include_member:
                continue
            if pack_type and row["pack_type"] != pack_type:
                continue

            volume = row["pvol"] or row["lvol"]
            abv = row["pabv"] if row["pabv"] is not None else row["labv"]
            source = row["abv_source"] if row["pabv"] is not None else (retailer if abv is not None else None)

            product = products.setdefault(
                row["product_id"],
                {
                    "id": row["product_id"],
                    "name": row["pname"] or row["lname"],
                    "unit_volume_ml": volume,
                    "abv": abv,
                    "abv_source": source,
                    "retailers": {},
                },
            )
            entry = product["retailers"].setdefault(
                retailer,
                {"url": row["url"], "sku": row["retailer_sku"], "options": []},
            )
            entry["options"].append(_option(row, abv, source, volume, now))

    for product in products.values():
        for entry in product["retailers"].values():
            entry["options"].sort(key=lambda o: (o["units"], o["member_only"]))
            entry["best_value"] = _best(entry["options"], "price_per_standard_drink")
            entry["cheapest_unit"] = _best(entry["options"], "unit_price")
            entry["last_updated"] = max(o["observed_at"] for o in entry["options"])
            entry["stale"] = all(o["stale"] for o in entry["options"])

        _mark_best(product)

    return list(products.values())


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


def compare(conn, postcode, q="", sort="value", pack_type=None, include_member=False,
            retailers=None, min_abv=None, max_abv=None, min_retailers=1,
            limit=50, offset=0, now=None):
    if sort not in SORTS:
        raise ValueError(f"sort must be one of {sorted(SORTS)}")
    if pack_type and pack_type not in PACK_TYPES:
        raise ValueError(f"pack must be one of {sorted(PACK_TYPES)}")

    locations = resolve_locations(conn, postcode)
    products = load_products(conn, locations, include_member, pack_type, now)
    tokens = q.lower().split()
    wanted = set(retailers) if retailers else None
    result = []

    for p in products:
        if tokens and not all(t in p["name"].lower() for t in tokens):
            continue
        if wanted and not wanted & set(p["retailers"]):
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

    INF = float("inf")
    keys = {
        "value": lambda p: (p["min_price_per_standard_drink"] is None,
                            p["min_price_per_standard_drink"] or INF, p["name"]),
        "unit_price": lambda p: (p["min_unit_price"] is None,
                                 p["min_unit_price"] or INF, p["name"]),
        "abv": lambda p: (p["abv"] is None, -(p["abv"] or 0), p["name"]),
        "name": lambda p: p["name"].lower(),
    }
    result.sort(key=keys[sort])

    total = len(result)
    latest = [loc["latest"] for loc in locations["retailers"].values() if loc]

    return {
        "meta": {
            "total": total,
            "limit": limit,
            "offset": offset,
            "demo": get_meta(conn, "demo") == "1",
            "latest_data": max(latest) if latest else None,
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


def product_detail(conn, product_id, postcode, now=None):
    now = now or datetime.now(timezone.utc)
    locations = resolve_locations(conn, postcode)
    products = [
        p for p in load_products(conn, locations, include_member=True, now=now)
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
