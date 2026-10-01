"""Crowd-contribution pipeline: validate -> stage -> quorum-promote.

Contributors (a browser extension, later) send the product JSON their own
browser already received. We parse it server-side with the tested parsers,
keep ONLY parsed fields (the raw payload is discarded), stage the prices, and
promote a price into the real, append-only history only when independent
contributors agree. See docs/CONTRIBUTION_API.md.

Privacy: the install id is stored only as a salted hash; no IP address is
read or stored by this module.
"""
import hashlib
import hmac
import json
import os
from datetime import datetime, timedelta, timezone
from typing import Literal, Optional

from pydantic import BaseModel, Field

from common import db
from common.pipeline import run_matching
from common.postcodes import state_for_postcode
from common.records import Location, Retailer
from scrapers import bws, endeavour, liquorland

MAX_BODY_BYTES = 3_000_000
MAX_PRODUCTS = 600
MAX_PER_HOUR = 30
QUORUM = 2                 # default: independent installs that must agree to the cent
                           # (BEEROO_QUORUM=1 = single-user mode for your own local copy)
WINDOW = timedelta(hours=24)
EXPIRY = timedelta(hours=72)
SINGLE_MIN_ACCEPTED = 10   # a lone contributor needs this track record...
SINGLE_MIN_TRUST = 0.85
SINGLE_MAX_MOVE = 0.15     # ...and the price within 15% of the last accepted one
LARGE_MOVE = 0.30          # a move this big vs the last accepted price needs QUORUM + 1
DRIFT_ERROR_SHARE = 0.30
DRIFT_WINDOW = 20

KINDS = {
    "dan_murphys_browse": Retailer.DAN_MURPHYS,
    "bws_products": Retailer.BWS,
    "liquorland_products": Retailer.LIQUORLAND,
}
# Parse "errors" that just mean the item isn't buyable - not format drift.
BENIGN_ERRORS = {"no available online prices", "unavailable", "no online prices", "no price"}
STATES = Literal["NSW", "VIC", "QLD", "SA", "WA", "TAS", "NT", "ACT"]


class ContribError(Exception):
    def __init__(self, status, message):
        super().__init__(message)
        self.status, self.message = status, message


class LocationIn(BaseModel):
    store_id: str = Field(pattern=r"^[A-Za-z0-9_-]{1,20}$")
    store_name: Optional[str] = Field(default=None, max_length=80)
    suburb: Optional[str] = Field(default=None, max_length=80)
    state: STATES
    postcode: str = Field(pattern=r"^\d{4}$")


class ContributionIn(BaseModel):
    schema_version: Literal[1]
    install_id: str = Field(pattern=r"^[0-9a-fA-F-]{36}$")
    extension_version: str = Field(max_length=20)
    kind: Literal["dan_murphys_browse", "bws_products", "liquorland_products"]
    logged_in: bool
    location: Optional[LocationIn] = None
    payload: dict


def _salt():
    return os.environ.get("BEEROO_SALT", "dev-only-salt").encode()


def hash_install(install_id):
    return hmac.new(_salt(), install_id.lower().encode(), hashlib.sha256).hexdigest()[:32]


def payload_digest(payload):
    blob = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(blob).hexdigest()


def _iso(dt):
    return dt.isoformat()


# --------------------------------------------------------------------------
# Validation + parsing
# --------------------------------------------------------------------------


def resolve_location(kind, location, payload):
    if kind == "liquorland_products":
        site = liquorland.site_state(payload)
        if site is None:
            raise ContribError(422, "parse: Liquorland response has no site state")
        return liquorland.location_for_site(site)

    if location is None:
        raise ContribError(422, "location is required for this kind of contribution")

    expected = state_for_postcode(location.postcode)
    if expected is None or expected != location.state:
        raise ContribError(422, "store postcode does not match its state")

    return Location(
        retailer=KINDS[kind],
        store_id=location.store_id,
        store_name=location.store_name,
        suburb=location.suburb,
        state=location.state,
        postcode=location.postcode,
    )


def parse_payload(kind, payload, location, now):
    key = location.location_key
    if kind == "dan_murphys_browse":
        return endeavour.parse_browse_payload(payload, key, now, Retailer.DAN_MURPHYS, endeavour.DM_BASE_URL)
    if kind == "bws_products":
        return bws.parse_bws_payload(payload, key, now)
    return liquorland.parse_liquorland_payload(payload, key, now)


def drift_share(errors):
    """Share of parse errors that are NOT benign 'not buyable' reasons."""
    total = len(errors)
    if not total:
        return 0.0
    bad = sum(1 for _, reason in errors if reason not in BENIGN_ERRORS)
    return bad / total


# --------------------------------------------------------------------------
# Staging
# --------------------------------------------------------------------------


def _record(conn, install_hash, retailer, location_key, now, digest, n_products, n_errors, status, reason=None):
    cur = conn.execute(
        "INSERT INTO contributions (install_hash, retailer, location_key, received_at, payload_hash, "
        "n_products, n_errors, status, reason) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (install_hash, retailer, location_key, _iso(now), digest, n_products, n_errors, status, reason),
    )
    return cur.lastrowid


def _touch_install(conn, install_hash, now):
    conn.execute(
        "INSERT OR IGNORE INTO installs (install_hash, first_seen) VALUES (?, ?)",
        (install_hash, _iso(now)),
    )


def accept_contribution(conn, body, now=None):
    """Validate, parse and stage one contribution. Returns a result dict.
    Raises ContribError for rejections (and records them for health stats)."""
    now = now or datetime.now(timezone.utc)

    if body.logged_in:
        raise ContribError(422, "contributions from logged-in sessions are not accepted")

    install_hash = hash_install(body.install_id)
    retailer = KINDS[body.kind].value
    _touch_install(conn, install_hash, now)

    recent = conn.execute(
        "SELECT COUNT(*) c FROM contributions WHERE install_hash = ? AND received_at >= ?",
        (install_hash, _iso(now - timedelta(hours=1))),
    ).fetchone()["c"]
    if recent >= MAX_PER_HOUR:
        raise ContribError(429, "too many contributions; try again later")

    digest = payload_digest(body.payload)
    dup = conn.execute(
        "SELECT id FROM contributions WHERE install_hash = ? AND payload_hash = ? "
        "AND status = 'accepted' AND received_at >= ?",
        (install_hash, digest, _iso(now - WINDOW)),
    ).fetchone()
    if dup:
        return {"status": "duplicate", "contribution_id": dup["id"], "products": 0, "staged": 0}

    def reject(status, message, location_key=None, n_products=0, n_errors=0):
        _record(conn, install_hash, retailer, location_key, now, digest, n_products, n_errors, "rejected", message)
        conn.commit()
        raise ContribError(status, message)

    try:
        location = resolve_location(body.kind, body.location, body.payload)
    except ContribError as e:
        reject(e.status, e.message)

    try:
        products, errors = parse_payload(body.kind, body.payload, location, now)
    except Exception:
        reject(422, "parse: payload is not in the expected format", location.location_key)

    if len(products) > MAX_PRODUCTS:
        reject(422, f"too many products ({len(products)} > {MAX_PRODUCTS})", location.location_key)
    if drift_share(errors) > DRIFT_ERROR_SHARE:
        reject(422, "parse: too many unrecognised items (format may have changed)",
               location.location_key, len(products), len(errors))
    if not products:
        reject(422, "parse: no usable products in payload", location.location_key, 0, len(errors))

    contribution_id = _record(
        conn, install_hash, retailer, location.location_key, now, digest,
        len(products), len(errors), "accepted",
    )
    db.upsert_location(conn, location)

    staged, touched = 0, set()
    for product in products:
        listing = product.listing
        for obs in product.prices:
            conn.execute(
                "INSERT INTO staged_observations (contribution_id, install_hash, retailer, location_key, "
                "retailer_sku, pack_type, units, member_only, price, received_at, url, name, brand, "
                "category, abv, unit_volume_ml) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (contribution_id, install_hash, retailer, location.location_key, listing.retailer_sku,
                 obs.pack_type.value, obs.units, int(obs.member_only), obs.price, _iso(now),
                 listing.url, listing.name, listing.brand, listing.category, listing.abv,
                 listing.unit_volume_ml),
            )
            staged += 1
            touched.add((retailer, location.location_key, listing.retailer_sku,
                         obs.pack_type.value, obs.units, int(obs.member_only)))

    conn.commit()
    stats = promote(conn, now, touched)

    return {
        "status": "accepted",
        "contribution_id": contribution_id,
        "products": len(products),
        "staged": staged,
        "skipped": len(errors),
        "promoted": stats["promoted"],
        "pending": stats["still_pending"],
    }


# --------------------------------------------------------------------------
# Quorum promotion
# --------------------------------------------------------------------------


def trust(conn, install_hash):
    row = conn.execute("SELECT accepted, rejected FROM installs WHERE install_hash = ?", (install_hash,)).fetchone()
    if not row:
        return 0.0, 0
    return (row["accepted"] + 1) / (row["accepted"] + row["rejected"] + 2), row["accepted"]


def _last_accepted_price(conn, retailer, key):
    _, location_key, sku, pack_type, units, member = key
    row = conn.execute(
        "SELECT o.price FROM price_observations o JOIN listings l ON l.id = o.listing_id "
        "WHERE l.retailer = ? AND l.retailer_sku = ? AND o.location_key = ? AND o.pack_type = ? "
        "AND o.units = ? AND o.member_only = ? ORDER BY o.observed_at DESC, o.id DESC LIMIT 1",
        (retailer, sku, location_key, pack_type, units, member),
    ).fetchone()
    return row["price"] if row else None


def quorum():
    try:
        return max(1, int(os.environ.get("BEEROO_QUORUM", QUORUM)))
    except ValueError:
        return QUORUM


def _min_install_age():
    return timedelta(hours=float(os.environ.get("BEEROO_MIN_INSTALL_AGE_HOURS", "0")))


def _decide(conn, key, rows, now=None):
    """Return (winning_price, winner_rows, outlier_rows) or None to hold."""
    now = now or datetime.now(timezone.utc)
    min_age = _min_install_age()
    latest = {}
    for r in sorted(rows, key=lambda r: r["received_at"]):
        if min_age:
            seen = conn.execute(
                "SELECT first_seen FROM installs WHERE install_hash = ?", (r["install_hash"],)
            ).fetchone()
            if not seen or now - datetime.fromisoformat(seen["first_seen"]) < min_age:
                continue  # too-new installs don't get a vote (Sybil resistance)
        latest[r["install_hash"]] = r  # newest per install wins

    by_price = {}
    for r in latest.values():
        by_price.setdefault(r["price"], []).append(r)

    ranked = sorted(by_price.items(), key=lambda kv: -len(kv[1]))

    need = quorum()
    if ranked and len(ranked[0][1]) >= need:
        if len(ranked) > 1 and len(ranked[1][1]) == len(ranked[0][1]):
            return None  # ambiguous: two equally supported prices
        price, winners = ranked[0]
        last = _last_accepted_price(conn, key[0], key)
        # big swings need extra confirmation (not in single-user mode: you ARE the source)
        if need >= 2 and last and abs(price - last) / last > LARGE_MOVE and len(winners) < need + 1:
            return None
        outliers = [r for p, rs in ranked[1:] for r in rs]
        return price, winners, outliers

    if len(latest) == 1:  # a lone, proven contributor close to the last known price
        (row,) = latest.values()
        score, accepted = trust(conn, row["install_hash"])
        last = _last_accepted_price(conn, key[0], key)
        if (last and accepted >= SINGLE_MIN_ACCEPTED and score >= SINGLE_MIN_TRUST
                and abs(row["price"] - last) / last <= SINGLE_MAX_MOVE):
            return row["price"], [row], []

    return None


def promote(conn, now=None, keys=None):
    """Promote staged prices that reach consensus into the real history.
    `keys` limits the pass to the given series (default: all pending)."""
    now = now or datetime.now(timezone.utc)
    cutoff = _iso(now - WINDOW)

    if keys is None:
        keys = {
            (r["retailer"], r["location_key"], r["retailer_sku"], r["pack_type"], r["units"], r["member_only"])
            for r in conn.execute(
                "SELECT DISTINCT retailer, location_key, retailer_sku, pack_type, units, member_only "
                "FROM staged_observations WHERE status = 'pending'"
            )
        }

    promoted, pending, new_listing = 0, 0, False

    for key in keys:
        rows = conn.execute(
            "SELECT * FROM staged_observations WHERE retailer = ? AND location_key = ? AND retailer_sku = ? "
            "AND pack_type = ? AND units = ? AND member_only = ? AND status = 'pending' AND received_at >= ?",
            (*key, cutoff),
        ).fetchall()
        if not rows:
            continue

        decision = _decide(conn, key, rows, now)
        if decision is None:
            pending += 1
            continue

        price, winners, outliers = decision
        first = winners[0]
        from common.records import Listing, PackType, PriceObservation  # local: avoids import cycle noise

        listing = Listing(
            retailer=Retailer(first["retailer"]), retailer_sku=first["retailer_sku"], url=first["url"],
            name=first["name"], brand=first["brand"], category=first["category"],
            abv=first["abv"], unit_volume_ml=first["unit_volume_ml"],
        )
        existed = conn.execute(
            "SELECT 1 FROM listings WHERE retailer = ? AND retailer_sku = ?",
            (first["retailer"], first["retailer_sku"]),
        ).fetchone()
        when = max(datetime.fromisoformat(w["received_at"]) for w in winners)
        listing_id = db.upsert_listing(conn, listing, when)
        new_listing = new_listing or not existed
        db.record_observation(conn, listing_id, PriceObservation(
            pack_type=PackType(first["pack_type"]), units=first["units"], price=price,
            member_only=bool(first["member_only"]), location_key=first["location_key"], observed_at=when,
        ))

        win_ids = [w["id"] for w in winners]
        out_ids = [o["id"] for o in outliers]
        _mark(conn, win_ids, "promoted")
        _mark(conn, out_ids, "outlier")
        _bump(conn, {w["install_hash"] for w in winners}, accepted=1)
        _bump(conn, {o["install_hash"] for o in outliers}, rejected=1)
        # other pending duplicates from the same installs (older rows) are superseded
        conn.execute(
            "UPDATE staged_observations SET status = 'superseded' WHERE retailer = ? AND location_key = ? "
            "AND retailer_sku = ? AND pack_type = ? AND units = ? AND member_only = ? AND status = 'pending'",
            key,
        )
        promoted += 1

    conn.commit()

    if new_listing:
        run_matching(conn)

    return {"promoted": promoted, "still_pending": pending}


def _mark(conn, ids, status):
    for i in ids:
        conn.execute("UPDATE staged_observations SET status = ? WHERE id = ?", (status, i))


def _bump(conn, hashes, accepted=0, rejected=0):
    for h in hashes:
        conn.execute(
            "UPDATE installs SET accepted = accepted + ?, rejected = rejected + ? WHERE install_hash = ?",
            (accepted, rejected, h),
        )


def expire(conn, now=None):
    """Expire pending prices nobody confirmed within EXPIRY. Returns count."""
    now = now or datetime.now(timezone.utc)
    cur = conn.execute(
        "UPDATE staged_observations SET status = 'expired' WHERE status = 'pending' AND received_at < ?",
        (_iso(now - EXPIRY),),
    )
    conn.commit()
    return cur.rowcount


# --------------------------------------------------------------------------
# Health
# --------------------------------------------------------------------------


def health(conn, now=None):
    now = now or datetime.now(timezone.utc)
    since = _iso(now - timedelta(hours=24))
    out = {}

    for retailer in (r.value for r in KINDS.values()):
        rows = conn.execute(
            "SELECT status, reason FROM contributions WHERE retailer = ? AND received_at >= ?",
            (retailer, since),
        ).fetchall()
        recent = conn.execute(
            "SELECT status, reason FROM contributions WHERE retailer = ? ORDER BY id DESC LIMIT ?",
            (retailer, DRIFT_WINDOW),
        ).fetchall()
        parse_fail = sum(1 for r in recent if r["status"] == "rejected" and (r["reason"] or "").startswith("parse"))
        out[retailer] = {
            "accepted_24h": sum(r["status"] == "accepted" for r in rows),
            "rejected_24h": sum(r["status"] == "rejected" for r in rows),
            "drift_suspected": len(recent) >= 5 and parse_fail / len(recent) >= 0.5,
        }

    pending = conn.execute("SELECT COUNT(*) c FROM staged_observations WHERE status = 'pending'").fetchone()["c"]
    return {"retailers": out, "pending_prices": pending}


# --------------------------------------------------------------------------
# Trusted (first-party) ingest: our own scheduled scraper / manual captures
# --------------------------------------------------------------------------


class IngestIn(BaseModel):
    kind: Literal["dan_murphys_browse", "bws_products", "liquorland_products"]
    location: Optional[LocationIn] = None
    payload: dict
    observed_at: Optional[datetime] = None


def ingest_trusted(conn, body, now=None):
    """Write prices from a trusted source straight into the price history
    (no quorum). Auth is the caller's job (admin token)."""
    now = now or datetime.now(timezone.utc)
    when = body.observed_at or now

    if when > now + timedelta(minutes=5):
        raise ContribError(422, "observed_at is in the future")
    if when.tzinfo is None:
        when = when.replace(tzinfo=timezone.utc)

    location = resolve_location(body.kind, body.location, body.payload)

    try:
        products, errors = parse_payload(body.kind, body.payload, location, when)
    except Exception:
        raise ContribError(422, "parse: payload is not in the expected format")

    if drift_share(errors) > DRIFT_ERROR_SHARE:
        raise ContribError(422, "parse: too many unrecognised items (format may have changed)")
    if not products:
        raise ContribError(422, "parse: no usable products in payload")

    before = conn.execute("SELECT COUNT(*) c FROM listings").fetchone()["c"]
    db.upsert_location(conn, location)
    inserted = db.save_products(conn, products, when)
    after = conn.execute("SELECT COUNT(*) c FROM listings").fetchone()["c"]

    if after > before:
        run_matching(conn)

    return {
        "status": "ingested",
        "location_key": location.location_key,
        "products": len(products),
        "new_observations": inserted,
        "new_listings": after - before,
        "skipped": len(errors),
    }
