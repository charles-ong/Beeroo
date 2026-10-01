import copy
import json
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app import contrib
from app.main import create_app
from common import db

F = Path(__file__).parent / "fixtures"
WODEN = {"store_id": "6723", "store_name": "Woden", "suburb": "Woden", "state": "ACT", "postcode": "2606"}
THORNLEIGH = {"store_id": "1546", "store_name": "Thornleigh", "suburb": "Thornleigh", "state": "NSW", "postcode": "2120"}


def install(n):
    return f"00000000-0000-4000-8000-{n:012d}"


def load(name):
    return json.loads((F / name).read_text())


BWS = load("bws_2606_products.json")


def with_price(payload, parent, units, price):
    """Copy of a BWS payload with one pack's shelf price changed."""
    p = copy.deepcopy(payload)
    for item in p["Items"]:
        if item["PackParentStockCode"] == parent:
            for prod in item["Products"]:
                units_ = next(a["Value"] for a in prod["AdditionalDetails"] if a["Name"] == "productunitquantity")
                if int(units_) == units:
                    prod["Price"] = price
    return p


def body(n, payload, kind="bws_products", location=WODEN, logged_in=False):
    return {"schema_version": 1, "install_id": install(n), "extension_version": "0.1.0",
            "kind": kind, "logged_in": logged_in, "location": dict(location) if location else None,
            "payload": payload}


@pytest.fixture()
def path(tmp_path):
    return str(tmp_path / "t.sqlite3")


@pytest.fixture()
def client(path):
    return TestClient(create_app(path))


@pytest.fixture()
def conn(path, client):
    c = db.connect(path)
    yield c
    c.close()


def observed(conn, sku="809797", units=24):
    return [r["price"] for r in conn.execute(
        "SELECT o.price FROM price_observations o JOIN listings l ON l.id = o.listing_id "
        "WHERE l.retailer_sku = ? AND o.units = ? AND o.member_only = 0 ORDER BY o.id", (sku, units))]


# ---- consensus ------------------------------------------------------------


def test_single_new_contributor_is_staged_not_promoted(client, conn):
    r = client.post("/api/contrib", json=body(1, BWS))
    assert r.status_code == 202
    data = r.json()
    assert data["status"] == "accepted" and data["products"] == 14 and data["staged"] > 40
    assert data["promoted"] == 0
    assert conn.execute("SELECT COUNT(*) c FROM price_observations").fetchone()["c"] == 0
    assert conn.execute("SELECT COUNT(*) c FROM staged_observations WHERE status='pending'").fetchone()["c"] == data["staged"]


def test_two_independent_contributors_promote_prices(client, conn):
    client.post("/api/contrib", json=body(1, BWS))
    r = client.post("/api/contrib", json=body(2, BWS))
    assert r.json()["promoted"] > 40
    assert observed(conn) == [58.0]
    # the contributed store now serves real comparison data
    data = client.get("/api/compare", params={"postcode": "2606", "q": "carlton dry"}).json()
    assert data["locations"]["retailers"]["bws"]["store_name"] == "Woden"
    assert any("bws" in p["retailers"] for p in data["products"])


def test_same_install_cannot_confirm_itself(client, conn):
    client.post("/api/contrib", json=body(1, BWS))
    changed = with_price(BWS, 809797, 24, 58.5)  # different payload, same install
    client.post("/api/contrib", json=body(1, changed))
    assert observed(conn) == []


def test_outlier_is_rejected_when_quorum_forms(client, conn):
    client.post("/api/contrib", json=body(1, BWS))
    client.post("/api/contrib", json=body(3, with_price(BWS, 809797, 24, 99.0)))
    client.post("/api/contrib", json=body(2, BWS))
    assert observed(conn) == [58.0]
    statuses = {r["status"] for r in conn.execute("SELECT status FROM staged_observations WHERE price = 99.0")}
    assert statuses == {"outlier"}
    liar = conn.execute("SELECT rejected FROM installs WHERE install_hash = ?", (contrib.hash_install(install(3)),)).fetchone()
    assert liar["rejected"] >= 1


def test_late_disagreeing_price_does_not_override_consensus(client, conn):
    client.post("/api/contrib", json=body(1, BWS))
    client.post("/api/contrib", json=body(2, BWS))
    client.post("/api/contrib", json=body(3, with_price(BWS, 809797, 24, 99.0)))
    assert observed(conn) == [58.0]


def test_two_way_disagreement_stays_pending(client, conn):
    client.post("/api/contrib", json=body(1, BWS))
    client.post("/api/contrib", json=body(2, with_price(BWS, 809797, 24, 62.0)))
    assert observed(conn) == []  # 58 vs 62, one vote each: no consensus


def test_new_quorum_at_a_new_price_is_a_real_price_change(client, conn):
    for n, price in ((1, 58.0), (2, 58.0), (3, 62.0), (4, 62.0)):
        client.post("/api/contrib", json=body(n, with_price(BWS, 809797, 24, price)))
    assert observed(conn) == [58.0, 62.0]


def test_equally_supported_prices_are_held(conn):
    key = ("bws", "bws:6723", "809797", "case", 24, 0)

    def row(i, install_hash, price):
        return {"id": i, "install_hash": install_hash, "price": price,
                "received_at": f"2026-10-01T0{i}:00:00+00:00"}

    tie = [row(1, "a", 58.0), row(2, "b", 58.0), row(3, "c", 62.0), row(4, "d", 62.0)]
    assert contrib._decide(conn, key, tie) is None
    clear = tie + [row(5, "e", 58.0)]
    price, winners, outliers = contrib._decide(conn, key, clear)
    assert price == 58.0 and len(winners) == 3 and len(outliers) == 2


def test_trusted_lone_contributor_can_update_within_15_percent(client, conn):
    client.post("/api/contrib", json=body(1, BWS))
    client.post("/api/contrib", json=body(2, BWS))
    conn.execute("UPDATE installs SET accepted = 20 WHERE install_hash = ?", (contrib.hash_install(install(5)),))
    conn.execute("INSERT OR IGNORE INTO installs (install_hash, first_seen, accepted) VALUES (?, '2026-01-01', 20)",
                 (contrib.hash_install(install(5)),))
    conn.commit()
    client.post("/api/contrib", json=body(5, with_price(BWS, 809797, 24, 55.0)))  # -5%
    assert observed(conn)[-1] == 55.0
    client.post("/api/contrib", json=body(5, with_price(BWS, 809797, 24, 30.0)))  # -45%: too big a jump alone
    assert observed(conn)[-1] == 55.0


def test_untrusted_lone_contributor_cannot_move_price(client, conn):
    client.post("/api/contrib", json=body(1, BWS))
    client.post("/api/contrib", json=body(2, BWS))
    client.post("/api/contrib", json=body(6, with_price(BWS, 809797, 24, 57.0)))
    assert observed(conn) == [58.0]


def test_liquorland_location_comes_from_payload_not_claim(client, conn):
    ll = load("liquorland_wa_products.json")
    lie = {**WODEN}  # claims ACT; payload says ll_wa
    for n in (1, 2):
        r = client.post("/api/contrib", json=body(n, ll, kind="liquorland_products", location=lie))
        assert r.status_code == 202
    keys = {r["location_key"] for r in conn.execute("SELECT location_key FROM price_observations")}
    assert keys == {"liquorland:ll_wa"}


def test_dan_murphys_contribution(client, conn):
    dm = load("dan_murphys_browse_page1.json")
    for n in (1, 2):
        r = client.post("/api/contrib", json=body(n, dm, kind="dan_murphys_browse", location=THORNLEIGH))
        assert r.status_code == 202
    loc = conn.execute("SELECT * FROM locations WHERE location_key = 'dan_murphys:1546'").fetchone()
    assert loc["state"] == "NSW"
    assert conn.execute("SELECT COUNT(*) c FROM price_observations").fetchone()["c"] > 20


# ---- validation -----------------------------------------------------------


def test_logged_in_contributions_rejected(client):
    r = client.post("/api/contrib", json=body(1, BWS, logged_in=True))
    assert r.status_code == 422 and "logged-in" in r.json()["detail"]


@pytest.mark.parametrize("loc,fragment", [
    ({**WODEN, "postcode": "6000"}, "does not match"),
    (None, "location is required"),
])
def test_location_validation(client, loc, fragment):
    r = client.post("/api/contrib", json=body(1, BWS, location=loc))
    assert r.status_code == 422 and fragment in r.json()["detail"]


@pytest.mark.parametrize("mutate", [
    lambda b: b.update(install_id="not-a-uuid"),
    lambda b: b.update(kind="evil"),
    lambda b: b.update(schema_version=2),
    lambda b: b["location"].update(state="XX"),
    lambda b: b["location"].update(store_id="a b; drop table"),
    lambda b: b.pop("payload"),
])
def test_schema_validation(client, mutate):
    b = body(1, BWS)
    mutate(b)
    assert client.post("/api/contrib", json=b).status_code == 422


def test_malformed_json_and_oversize(client):
    assert client.post("/api/contrib", content=b"{not json", headers={"content-type": "application/json"}).status_code == 422
    big = body(1, {"pad": "x" * (contrib.MAX_BODY_BYTES + 10)})
    assert client.post("/api/contrib", json=big).status_code == 413


def test_garbage_payload_rejected_and_recorded(client, conn):
    r = client.post("/api/contrib", json=body(1, {"hello": "world"}))
    assert r.status_code == 422 and r.json()["detail"].startswith("parse:")
    row = conn.execute("SELECT status, reason FROM contributions").fetchone()
    assert row["status"] == "rejected" and row["reason"].startswith("parse")


def test_format_drift_is_rejected_not_staged(client, conn):
    broken = copy.deepcopy(BWS)
    for item in broken["Items"]:
        del item["PackParentStockCode"]      # simulate a renamed field
    r = client.post("/api/contrib", json=body(1, broken))
    assert r.status_code == 422 and "format may have changed" in r.json()["detail"]
    assert conn.execute("SELECT COUNT(*) c FROM staged_observations").fetchone()["c"] == 0


def test_unavailable_items_are_not_treated_as_drift(client):
    # the fixture contains unavailable products; still accepted
    assert client.post("/api/contrib", json=body(1, BWS)).status_code == 202


def test_duplicate_payload_from_same_install_is_ignored(client, conn):
    client.post("/api/contrib", json=body(1, BWS))
    r = client.post("/api/contrib", json=body(1, BWS))
    assert r.status_code == 200 and r.json()["status"] == "duplicate"
    assert conn.execute("SELECT COUNT(*) c FROM contributions").fetchone()["c"] == 1


def test_rate_limit_per_install(client):
    for i in range(contrib.MAX_PER_HOUR):
        assert client.post("/api/contrib", json=body(1, with_price(BWS, 809797, 24, 40 + i))).status_code == 202
    assert client.post("/api/contrib", json=body(1, with_price(BWS, 809797, 24, 99))).status_code == 429
    assert client.post("/api/contrib", json=body(2, BWS)).status_code == 202  # other installs unaffected


# ---- privacy --------------------------------------------------------------


def test_raw_install_id_and_payload_are_never_stored(client, path):
    client.post("/api/contrib", json=body(1, BWS))
    client.post("/api/contrib", json=body(2, BWS))
    raw = sqlite3.connect(path)
    dump = "\n".join(raw.iterdump())
    assert install(1) not in dump and install(2) not in dump
    assert "PackParentStockCode" not in dump and "RichDescription" not in dump
    cols = {c[1] for t in ("contributions", "installs", "staged_observations")
            for c in raw.execute(f"PRAGMA table_info({t})")}
    assert not cols & {"ip", "ip_address", "user_agent", "payload", "install_id"}


# ---- operations -----------------------------------------------------------


def test_expiry_drops_unconfirmed_prices(client, conn):
    client.post("/api/contrib", json=body(1, BWS))
    future = datetime.now(timezone.utc) + contrib.EXPIRY + timedelta(hours=1)
    assert contrib.expire(conn, future) > 40
    assert conn.execute("SELECT COUNT(*) c FROM staged_observations WHERE status='pending'").fetchone()["c"] == 0


def test_confirmation_after_window_does_not_count(conn):
    old = datetime.now(timezone.utc) - timedelta(hours=30)
    first = contrib.ContributionIn.model_validate(body(1, BWS))
    contrib.accept_contribution(conn, first, now=old)
    second = contrib.ContributionIn.model_validate(body(2, BWS))
    contrib.accept_contribution(conn, second)  # now; first is outside the 24h window
    assert observed(conn) == []


def test_admin_promote_requires_token(client, monkeypatch):
    assert client.post("/api/contrib/promote").status_code == 403
    monkeypatch.setenv("BEEROO_ADMIN_TOKEN", "s3cret")
    assert client.post("/api/contrib/promote", headers={"X-Admin-Token": "wrong"}).status_code == 403
    ok = client.post("/api/contrib/promote", headers={"X-Admin-Token": "s3cret"})
    assert ok.status_code == 200 and set(ok.json()) == {"promoted", "still_pending", "expired"}


def test_health_reports_counts_and_drift(client):
    client.post("/api/contrib", json=body(1, BWS))
    for n in range(10, 17):
        client.post("/api/contrib", json=body(n, {"nothing": n}))
    h = client.get("/api/contrib/health").json()
    assert h["retailers"]["bws"]["accepted_24h"] == 1
    assert h["retailers"]["bws"]["rejected_24h"] == 7
    assert h["retailers"]["bws"]["drift_suspected"] is True
    assert h["retailers"]["liquorland"]["drift_suspected"] is False
    assert h["pending_prices"] > 0


def test_promotion_makes_new_products_visible_after_matching(client):
    for n in (1, 2):
        client.post("/api/contrib", json=body(n, BWS))
    for n in (3, 4):
        client.post("/api/contrib", json=body(n, load("liquorland_act_products.json"), kind="liquorland_products", location=WODEN))
    data = client.get("/api/compare", params={"postcode": "2606", "min_retailers": 2, "limit": 100}).json()
    assert data["meta"]["total"] >= 1  # BWS + Liquorland products matched into shared products


def test_large_price_swings_need_an_extra_confirmation(client, conn):
    client.post("/api/contrib", json=body(1, BWS))
    client.post("/api/contrib", json=body(2, BWS))                      # 58 established
    cheap = with_price(BWS, 809797, 24, 20.0)                            # -66%
    client.post("/api/contrib", json=body(3, cheap))
    client.post("/api/contrib", json=body(4, cheap))
    assert observed(conn) == [58.0]                                      # two is not enough
    client.post("/api/contrib", json=body(5, cheap))
    assert observed(conn) == [58.0, 20.0]                                # three is


def test_new_installs_dont_vote_when_min_age_is_set(client, conn, monkeypatch):
    monkeypatch.setenv("BEEROO_MIN_INSTALL_AGE_HOURS", "24")
    client.post("/api/contrib", json=body(1, BWS))
    client.post("/api/contrib", json=body(2, BWS))
    assert observed(conn) == []                                          # both installs are brand new
    old = (datetime.now(timezone.utc) - timedelta(days=3)).isoformat()
    conn.execute("UPDATE installs SET first_seen = ?", (old,))
    conn.commit()
    contrib.promote(conn)
    assert observed(conn) == [58.0]


# ---- single-user mode (BEEROO_QUORUM=1) -------------------------------------


def test_single_user_mode_accepts_one_contributor(client, conn, monkeypatch):
    monkeypatch.setenv("BEEROO_QUORUM", "1")
    r = client.post("/api/contrib", json=body(1, BWS))
    assert r.status_code == 202 and r.json()["promoted"] > 40
    assert observed(conn) == [58.0]


def test_single_user_mode_accepts_large_price_changes(client, conn, monkeypatch):
    monkeypatch.setenv("BEEROO_QUORUM", "1")
    client.post("/api/contrib", json=body(1, BWS))
    client.post("/api/contrib", json=body(1, with_price(BWS, 809797, 24, 20.0)))   # -66%
    assert observed(conn) == [58.0, 20.0]


def test_default_quorum_is_unchanged_and_bad_values_fall_back(client, conn, monkeypatch):
    assert contrib.quorum() == 2
    monkeypatch.setenv("BEEROO_QUORUM", "banana")
    assert contrib.quorum() == 2
    monkeypatch.setenv("BEEROO_QUORUM", "0")
    assert contrib.quorum() == 1                                                    # never below 1
    monkeypatch.setenv("BEEROO_QUORUM", "2")
    client.post("/api/contrib", json=body(1, BWS))
    assert observed(conn) == []
