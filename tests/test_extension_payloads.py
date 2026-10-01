"""Cross-language check: what the browser extension builds is accepted by the
server and parsed identically to the full, unfiltered captures."""
import json
import shutil
import subprocess
from datetime import datetime, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.main import create_app
from common import db
from scrapers.bws import parse_bws_payload
from scrapers.endeavour import parse_browse_payload
from scrapers.liquorland import parse_liquorland_payload

ROOT = Path(__file__).parent.parent
F = Path(__file__).parent / "fixtures"
NOW = datetime(2026, 10, 1, tzinfo=timezone.utc)

pytestmark = pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")


@pytest.fixture(scope="module")
def bodies():
    out = subprocess.run(
        ["node", str(ROOT / "extension" / "test" / "emit_sanitized.mjs")],
        capture_output=True, text=True, check=True, timeout=30,
    )
    return json.loads(out.stdout)


def snapshot(products):
    """Everything that matters to the app, order-independent."""
    return sorted(
        (
            p.listing.retailer_sku, p.listing.name, p.listing.brand, p.listing.abv,
            p.listing.unit_volume_ml, p.listing.url,
            tuple(sorted((o.pack_type.value, o.units, o.member_only, o.price) for o in p.prices)),
        )
        for p in products
    )


def test_dan_murphys_sanitised_parses_identically(bodies):
    raw = json.loads((F / "dan_murphys_browse_page1.json").read_text())
    clean = bodies["dan_murphys_browse"][0]["payload"]
    a, ea = parse_browse_payload(raw, "k", NOW)
    b, eb = parse_browse_payload(clean, "k", NOW)
    assert snapshot(a) == snapshot(b) and len(a) == 24
    assert ea == eb


def test_bws_sanitised_parses_identically(bodies):
    raw = json.loads((F / "bws_2606_products.json").read_text())
    clean = bodies["bws_products"][0]["payload"]
    a, ea = parse_bws_payload(raw, "k", NOW)
    b, eb = parse_bws_payload(clean, "k", NOW)
    assert snapshot(a) == snapshot(b) and len(a) == 14
    assert ea == eb


def test_liquorland_sanitised_parses_identically(bodies):
    raw = json.loads((F / "liquorland_act_products.json").read_text())
    clean = bodies["liquorland_products"][0]["payload"]
    a, ea = parse_liquorland_payload(raw, observed_at=NOW)
    b, eb = parse_liquorland_payload(clean, observed_at=NOW)
    assert snapshot(a) == snapshot(b) and len(a) == 40
    assert ea == eb


def test_server_accepts_what_the_extension_builds_and_reaches_consensus(bodies, tmp_path):
    client = TestClient(create_app(str(tmp_path / "t.sqlite3")))
    conn = db.connect(str(tmp_path / "t.sqlite3"))
    for kind, pair in bodies.items():
        first = client.post("/api/contrib", json=pair[0])
        second = client.post("/api/contrib", json=pair[1])
        assert first.status_code == 202, (kind, first.text)
        assert second.status_code == 202, (kind, second.text)
        assert first.json()["promoted"] == 0 and second.json()["promoted"] > 0, kind
    retailers = {r["retailer"] for r in conn.execute("SELECT DISTINCT retailer FROM listings")}
    assert retailers == {"dan_murphys", "bws", "liquorland"}
    data = client.get("/api/compare", params={"postcode": "2606", "limit": 200}).json()
    assert data["meta"]["total"] > 50


def test_extension_bodies_have_the_documented_shape(bodies):
    for kind, pair in bodies.items():
        b = pair[0]
        assert set(b) == {"schema_version", "install_id", "extension_version", "kind", "logged_in", "location", "payload"}
        assert b["schema_version"] == 1 and b["logged_in"] is False and b["kind"] == kind
        if kind == "liquorland_products":
            assert b["location"] is None
        else:
            assert set(b["location"]) == {"store_id", "store_name", "suburb", "state", "postcode"}
