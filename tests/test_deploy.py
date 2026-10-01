import json
import sqlite3
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.main import create_app
from common import db

F = Path(__file__).parent / "fixtures"
TOKEN = "t" * 32
WODEN = {"store_id": "6723", "store_name": "Woden", "suburb": "Woden", "state": "ACT", "postcode": "2606"}


def load(name):
    return json.loads((F / name).read_text())


@pytest.fixture()
def path(tmp_path):
    return str(tmp_path / "t.sqlite3")


@pytest.fixture()
def client(path, monkeypatch):
    monkeypatch.setenv("BEEROO_ADMIN_TOKEN", TOKEN)
    return TestClient(create_app(path))


def ingest(client, token=TOKEN, **over):
    body = {"kind": "bws_products", "location": WODEN, "payload": load("bws_2606_products.json"), **over}
    headers = {"X-Admin-Token": token} if token is not None else {}
    return client.post("/api/admin/ingest", json=body, headers=headers)


# ---- trusted ingest ---------------------------------------------------------


def test_ingest_requires_the_admin_token(client):
    assert ingest(client, token=None).status_code == 403
    assert ingest(client, token="wrong").status_code == 403


def test_ingest_without_server_token_configured_is_forbidden(path, monkeypatch):
    monkeypatch.delenv("BEEROO_ADMIN_TOKEN", raising=False)
    assert ingest(TestClient(create_app(path)), token="anything").status_code == 403


def test_ingest_writes_prices_immediately_without_quorum(client, path):
    r = ingest(client)
    assert r.status_code == 200
    data = r.json()
    assert data["status"] == "ingested" and data["location_key"] == "bws:6723"
    assert data["products"] == 14 and data["new_observations"] > 40 and data["new_listings"] == 14
    shown = client.get("/api/compare", params={"postcode": "2606"}).json()
    assert shown["meta"]["total"] == 14
    assert shown["locations"]["retailers"]["bws"]["store_name"] == "Woden"


def test_ingest_is_idempotent_for_the_same_day(client):
    ingest(client)
    again = ingest(client).json()
    assert again["new_observations"] == 0 and again["new_listings"] == 0


def test_ingest_uses_the_capture_time_and_rejects_the_future(client, path):
    r = ingest(client, observed_at="2026-09-01T00:00:00+00:00")
    assert r.status_code == 200
    first = sqlite3.connect(path).execute("SELECT MIN(observed_at) FROM price_observations").fetchone()[0]
    assert first.startswith("2026-09-01")
    assert ingest(client, observed_at="2999-01-01T00:00:00+00:00").status_code == 422


def test_ingest_validation(client):
    assert ingest(client, kind="evil").status_code == 422
    assert ingest(client, location={**WODEN, "postcode": "6000"}).status_code == 422   # state mismatch
    assert ingest(client, payload={"hello": "world"}).status_code == 422
    assert client.post("/api/admin/ingest", content=b"{nope", headers={"X-Admin-Token": TOKEN}).status_code == 422


def test_ingest_dan_murphys_and_liquorland(client):
    dm = ingest(client, kind="dan_murphys_browse", payload=load("dan_murphys_browse_page1.json"),
                location={"store_id": "1546", "store_name": "Thornleigh", "suburb": "Thornleigh", "state": "NSW", "postcode": "2120"})
    assert dm.status_code == 200 and dm.json()["products"] == 24
    ll = ingest(client, kind="liquorland_products", payload=load("liquorland_wa_products.json"), location=None)
    assert ll.status_code == 200 and ll.json()["location_key"] == "liquorland:ll_wa"


# ---- production guards ------------------------------------------------------


def test_production_refuses_to_start_without_secrets(path, monkeypatch):
    monkeypatch.setenv("BEEROO_ENV", "production")
    monkeypatch.delenv("BEEROO_SALT", raising=False)
    monkeypatch.delenv("BEEROO_ADMIN_TOKEN", raising=False)
    with pytest.raises(RuntimeError, match="BEEROO_SALT"):
        create_app(path)
    monkeypatch.setenv("BEEROO_SALT", "salty")
    with pytest.raises(RuntimeError, match="BEEROO_ADMIN_TOKEN"):
        create_app(path)
    monkeypatch.setenv("BEEROO_ADMIN_TOKEN", "short")
    with pytest.raises(RuntimeError, match="at least 24"):
        create_app(path)


@pytest.fixture()
def prod(path, monkeypatch):
    monkeypatch.setenv("BEEROO_ENV", "production")
    monkeypatch.setenv("BEEROO_SALT", "salty")
    monkeypatch.setenv("BEEROO_ADMIN_TOKEN", TOKEN)
    monkeypatch.delenv("BEEROO_CONTRIB_ENABLED", raising=False)
    return TestClient(create_app(path))


def test_production_keeps_public_contributions_closed_by_default(prod):
    body = {"schema_version": 1, "install_id": "00000000-0000-4000-8000-000000000001", "extension_version": "0.1.0",
            "kind": "bws_products", "logged_in": False, "location": WODEN, "payload": load("bws_2606_products.json")}
    assert prod.post("/api/contrib", json=body).status_code == 503


def test_production_contributions_can_be_opened_explicitly(path, monkeypatch):
    monkeypatch.setenv("BEEROO_ENV", "production")
    monkeypatch.setenv("BEEROO_SALT", "salty")
    monkeypatch.setenv("BEEROO_ADMIN_TOKEN", TOKEN)
    monkeypatch.setenv("BEEROO_CONTRIB_ENABLED", "1")
    c = TestClient(create_app(path))
    body = {"schema_version": 1, "install_id": "00000000-0000-4000-8000-000000000001", "extension_version": "0.1.0",
            "kind": "bws_products", "logged_in": False, "location": WODEN, "payload": load("bws_2606_products.json")}
    assert c.post("/api/contrib", json=body).status_code == 202


def test_production_hides_api_docs_but_admin_still_works(prod):
    assert prod.get("/api/docs").status_code == 404
    assert prod.get("/api/openapi.json").status_code == 404
    assert prod.post("/api/admin/ingest", json={}, headers={"X-Admin-Token": TOKEN}).status_code == 422


def test_hsts_only_in_production(prod, path, monkeypatch):
    assert "strict-transport-security" in {k.lower() for k in prod.get("/healthz").headers}
    monkeypatch.delenv("BEEROO_ENV")
    dev = TestClient(create_app(path))
    assert "strict-transport-security" not in {k.lower() for k in dev.get("/healthz").headers}


# ---- headers / health / db --------------------------------------------------


def test_security_headers_and_strict_csp(client):
    h = client.get("/").headers
    assert h["x-content-type-options"] == "nosniff"
    assert h["x-frame-options"] == "DENY"
    csp = h["content-security-policy"]
    assert "default-src 'self'" in csp and "script-src 'self'" in csp
    assert "unsafe-inline" not in csp and "unsafe-eval" not in csp


def test_frontend_is_csp_compatible():
    html = (Path(__file__).parent.parent / "app" / "static" / "index.html").read_text()
    js = (Path(__file__).parent.parent / "app" / "static" / "app.js").read_text()
    import re
    assert not re.search(r"<script(?![^>]*\bsrc=)", html) and "<style" not in html
    assert not re.search(r"\son\w+=", html) and 'style="' not in html
    assert "innerHTML" not in js and "eval(" not in js
    assert 'setAttribute("style"' not in js


def test_healthz(client):
    assert client.get("/healthz").json() == {"status": "ok", "products": 0, "latest_data": None}
    ingest(client)
    data = client.get("/healthz").json()
    assert data["products"] == 14 and data["latest_data"]


def test_database_uses_wal_and_a_busy_timeout(path):
    conn = db.connect(path)
    assert conn.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
    assert conn.execute("PRAGMA busy_timeout").fetchone()[0] == 5000
