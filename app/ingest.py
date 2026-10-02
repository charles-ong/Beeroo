"""Trusted ingest: write prices from our OWN scrapers straight into the price
history (no quorum). Used by the scheduled scrapers (locally or via the admin
endpoint). Auth for the HTTP endpoint is the admin token (see app/main.py).
"""
from datetime import datetime, timedelta, timezone
from typing import Literal, Optional

from pydantic import BaseModel, Field

from common import db
from common.pipeline import run_matching
from common.postcodes import state_for_postcode
from common.records import Location, Retailer
from scrapers import bws, dan_murphys_dom, endeavour, liquorland

KINDS = {
    "dan_murphys_browse": Retailer.DAN_MURPHYS,
    "dan_murphys_cards": Retailer.DAN_MURPHYS,
    "bws_products": Retailer.BWS,
    "liquorland_products": Retailer.LIQUORLAND,
}
MAX_BODY_BYTES = 12_000_000
# Parse "errors" that just mean the item isn't buyable here - not format drift.
BENIGN_ERRORS = {"no available online prices", "unavailable", "no online prices", "no price"}
DRIFT_ERROR_SHARE = 0.30
STATES = Literal["NSW", "VIC", "QLD", "SA", "WA", "TAS", "NT", "ACT"]


class IngestError(Exception):
    def __init__(self, status, message):
        super().__init__(message)
        self.status, self.message = status, message


class LocationIn(BaseModel):
    store_id: str = Field(pattern=r"^[A-Za-z0-9_-]{1,20}$")
    store_name: Optional[str] = Field(default=None, max_length=80)
    suburb: Optional[str] = Field(default=None, max_length=80)
    state: STATES
    postcode: str = Field(pattern=r"^\d{4}$")


def resolve_location(kind, location, payload):
    if kind == "liquorland_products":
        site = liquorland.site_state(payload)
        if site is None:
            raise IngestError(422, "parse: Liquorland response has no site state")
        return liquorland.location_for_site(site)

    if location is None:
        raise IngestError(422, "location is required for this kind of payload")

    expected = state_for_postcode(location.postcode)
    if expected is None or expected != location.state:
        raise IngestError(422, "store postcode does not match its state")

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
    if kind == "dan_murphys_cards":
        return dan_murphys_dom.parse_cards_payload(payload, key, now)
    if kind == "bws_products":
        return bws.parse_bws_payload(payload, key, now)
    return liquorland.parse_liquorland_payload(payload, key, now)


def drift_share(errors, total=None):
    """Share of parse errors that are NOT benign 'not buyable' reasons. With
    `total` (how many items there were), the share of ALL items that failed
    for a real reason: a handful of odd cards must not sink a whole page."""
    total = total or len(errors)
    if not total:
        return 0.0
    bad = sum(1 for _, reason in errors if reason not in BENIGN_ERRORS)
    return bad / total


def page_size(kind, payload):
    return len(payload.get("cards") or []) if kind == "dan_murphys_cards" and isinstance(payload.get("cards"), list) else None


class IngestIn(BaseModel):
    kind: Literal["dan_murphys_browse", "dan_murphys_cards", "bws_products", "liquorland_products"]
    location: Optional[LocationIn] = None
    payload: dict
    observed_at: Optional[datetime] = None


def ingest_trusted(conn, body, now=None):
    """Write prices from a trusted source straight into the price history
    (no quorum). Auth is the caller's job (admin token)."""
    now = now or datetime.now(timezone.utc)
    when = body.observed_at or now

    if when > now + timedelta(minutes=5):
        raise IngestError(422, "observed_at is in the future")
    if when.tzinfo is None:
        when = when.replace(tzinfo=timezone.utc)

    location = resolve_location(body.kind, body.location, body.payload)

    try:
        products, errors = parse_payload(body.kind, body.payload, location, when)
    except Exception:
        raise IngestError(422, "parse: payload is not in the expected format")

    if drift_share(errors, page_size(body.kind, body.payload)) > DRIFT_ERROR_SHARE:
        raise IngestError(422, "parse: too many unrecognised items (format may have changed)")
    if not products:
        raise IngestError(422, "parse: no usable products in payload")

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
