import json
import sys
import threading
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))
import scheduled_scrape as ss  # noqa: E402

from app.main import create_app  # noqa: E402
from common import db  # noqa: E402
from common.records import Location, Retailer  # noqa: E402
from scrapers.dan_murphys import Blocked  # noqa: E402

F = Path(__file__).parent / "fixtures"
NOW = datetime(2026, 10, 1, 1, 0, tzinfo=timezone.utc)
PAGE = json.loads((F / "dan_murphys_browse_page1.json").read_text())
STORE = Location(retailer=Retailer.DAN_MURPHYS, store_id="1546", store_name="Thornleigh",
                 suburb="Thornleigh", state="NSW", postcode="2120")


def fake_scrape(total=24, collected=24, pages=1, store=STORE, calls=None):
    async def scrape(postcode, max_pages):
        if calls is not None:
            calls.append((postcode, max_pages))
        return {"location": store, "raw_pages": [PAGE] * pages, "products": [object()] * collected,
                "errors": [], "total": total}
    return scrape


def blocked_scrape(calls):
    async def scrape(postcode, max_pages):
        calls.append(postcode)
        raise Blocked("bot protection page served (title='Attention Required! | Cloudflare')")
    return scrape


@pytest.fixture()
def state(tmp_path):
    return str(tmp_path / "state.json")


def go(state, scrape, push=None, now=NOW, **kw):
    pushed = []
    push = push or (lambda body: pushed.append(body) or {"products": 24, "new_observations": 58, "new_listings": 24})
    code, summary = ss.run(state_path=state, scrape=scrape, push=push, now=now, **kw)
    return code, summary, pushed


# ---- zone rotation ----------------------------------------------------------


def test_zone_rotation_starts_in_order_then_least_recent():
    assert ss.pick_zone({"zones": {}}) == "2000"
    st = {"zones": {"2000": {"last_success": "2026-10-01"}}}
    assert ss.pick_zone(st) == "3000"
    st = {"zones": {z: {"last_success": f"2026-09-{10 + i}"} for i, z in enumerate(ss.ZONES)}}
    assert ss.pick_zone(st) == ss.ZONES[0]            # oldest success goes first
    st["zones"]["2000"]["last_success"] = "2026-10-01"
    assert ss.pick_zone(st) == "3000"


def test_failed_attempts_do_not_count_as_fresh():
    st = {"zones": {"2000": {"last_attempt": "2026-10-01", "last_error": "x"}}}
    assert ss.pick_zone(st) == "2000"                 # still has no success, still first


# ---- successful runs --------------------------------------------------------


def test_successful_run_pushes_pages_and_records_state(state):
    calls = []
    code, summary, pushed = go(state, fake_scrape(pages=3, calls=calls))
    assert code == 0 and summary["status"] == "ok"
    assert calls == [("2000", 40)] and len(pushed) == 3
    body = pushed[0]
    assert body["kind"] == "dan_murphys_browse" and body["payload"] == PAGE
    assert body["observed_at"] == NOW.isoformat()
    assert body["location"] == {"store_id": "1546", "store_name": "Thornleigh", "suburb": "Thornleigh", "state": "NSW", "postcode": "2120"}
    assert summary["new_observations"] == 58 * 3
    saved = ss.load_state(state)
    assert saved["zones"]["2000"]["last_success"] == NOW.isoformat()
    assert saved["blocked"] == {"consecutive": 0, "backoff_until": None}
    # next run rotates to the next zone
    calls.clear()
    go(state, fake_scrape(calls=calls), now=NOW + timedelta(days=1))
    assert calls[0][0] == "3000"


def test_partial_result_is_pushed_but_not_marked_fresh(state):
    code, summary, pushed = go(state, fake_scrape(total=400, collected=120))
    assert code == 0 and summary["status"] == "partial" and pushed
    assert "last_success" not in ss.load_state(state)["zones"]["2000"]


def test_unknown_total_counts_as_incomplete(state):
    _, summary, _ = go(state, fake_scrape(total=None))
    assert summary["status"] == "partial"


def test_forced_zone_and_jitter(state):
    slept = []
    calls = []
    go(state, fake_scrape(calls=calls), zone="6000", max_pages=3, jitter=600, sleep=slept.append)
    assert calls == [("6000", 3)] and len(slept) == 1 and 0 <= slept[0] <= 600


# ---- blocking and backoff ---------------------------------------------------


def test_a_block_stops_the_run_and_backs_off_exponentially(state):
    calls = []
    code, summary, pushed = go(state, blocked_scrape(calls))
    assert code == 3 and summary == {"status": "blocked", "backoff_days": 2, "zone": "2000"}
    assert pushed == [] and calls == ["2000"]
    st = ss.load_state(state)
    assert st["blocked"]["consecutive"] == 1
    assert datetime.fromisoformat(st["blocked"]["backoff_until"]) == NOW + timedelta(days=2)

    # still inside the backoff window: the browser is never even launched
    code, summary, _ = go(state, blocked_scrape(calls), now=NOW + timedelta(days=1))
    assert code == 0 and summary["status"] == "backing_off" and calls == ["2000"]

    # window over: tries again; blocked again -> 4 days
    code, summary, _ = go(state, blocked_scrape(calls), now=NOW + timedelta(days=3))
    assert code == 3 and summary["backoff_days"] == 4 and len(calls) == 2


def test_backoff_is_capped(state):
    st = {"zones": {}, "blocked": {"consecutive": 9, "backoff_until": None}}
    ss.save_state(state, st)
    _, summary, _ = go(state, blocked_scrape([]))
    assert summary["backoff_days"] == ss.MAX_BACKOFF_DAYS


def test_success_after_backoff_resets_the_counter(state):
    go(state, blocked_scrape([]))
    go(state, fake_scrape(), now=NOW + timedelta(days=3))
    assert ss.load_state(state)["blocked"] == {"consecutive": 0, "backoff_until": None}


def test_a_block_is_never_retried_within_the_same_run(state):
    calls = []
    go(state, blocked_scrape(calls))
    assert len(calls) == 1


# ---- other failures ---------------------------------------------------------


def test_scrape_error_is_recorded_without_backoff(state):
    async def boom(postcode, max_pages):
        raise RuntimeError("could not determine the active store")
    code, summary, pushed = go(state, boom)
    assert code == 1 and summary["status"] == "error" and pushed == []
    st = ss.load_state(state)
    assert "active store" in st["zones"]["2000"]["last_error"]
    assert st["blocked"]["backoff_until"] is None


def test_push_failure_is_an_error_and_not_a_success(state):
    def bad(body):
        raise RuntimeError("could not reach server")
    code, summary, _ = go(state, fake_scrape(), push=bad)
    assert code == 1 and summary["status"] == "push_failed"
    assert "last_success" not in ss.load_state(state)["zones"]["2000"]


def test_corrupt_state_file_is_treated_as_empty(state):
    Path(state).write_text("{not json")
    code, _, _ = go(state, fake_scrape())
    assert code == 0


# ---- end to end -------------------------------------------------------------


def test_end_to_end_into_the_real_app(state, tmp_path, monkeypatch):
    monkeypatch.setenv("BEEROO_ADMIN_TOKEN", "t" * 32)
    client = TestClient(create_app(str(tmp_path / "srv.sqlite3")))

    def push(body):
        r = client.post("/api/admin/ingest", json=body, headers={"X-Admin-Token": "t" * 32})
        assert r.status_code == 200, r.text
        return r.json()

    code, summary, _ = go(state, fake_scrape(), push=push)
    assert code == 0 and summary["new_listings"] == 24
    data = client.get("/api/compare", params={"postcode": "2120"}).json()
    assert data["meta"]["total"] == 24
    assert data["locations"]["retailers"]["dan_murphys"]["store_name"] == "Thornleigh"


def test_local_push_writes_to_a_database(tmp_path):
    path = tmp_path / "local.sqlite3"
    push = ss.local_push(path)
    r = push({"kind": "dan_murphys_browse", "location": ss.location_dict(STORE), "payload": PAGE,
              "observed_at": NOW.isoformat()})
    assert r["products"] == 24
    conn = db.connect(str(path))
    assert conn.execute("SELECT COUNT(*) c FROM listings").fetchone()["c"] == 24


# ---- http_push --------------------------------------------------------------


class Handler(BaseHTTPRequestHandler):
    log = []
    plan = []

    def do_POST(self):
        body = self.rfile.read(int(self.headers["content-length"]))
        Handler.log.append((self.headers.get("x-admin-token"), json.loads(body)))
        status, payload = Handler.plan.pop(0) if Handler.plan else (200, {"products": 1})
        self.send_response(status)
        self.end_headers()
        self.wfile.write(json.dumps(payload).encode())

    def log_message(self, *a):
        pass


@pytest.fixture()
def server():
    Handler.log, Handler.plan = [], []
    srv = HTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_port}"
    srv.shutdown()


def test_http_push_sends_token_header_and_json(server):
    out = ss.http_push(server, "tok")({"kind": "x"})
    assert out == {"products": 1}
    assert Handler.log == [("tok", {"kind": "x"})]


def test_http_push_retries_server_errors_but_not_rejections(server, monkeypatch):
    monkeypatch.setattr(ss.time, "sleep", lambda s: None)
    Handler.plan = [(503, {}), (200, {"products": 2})]
    assert ss.http_push(server, "tok")({"a": 1}) == {"products": 2}
    assert len(Handler.log) == 2
    Handler.log.clear()
    Handler.plan = [(422, {"detail": "bad"})]
    with pytest.raises(RuntimeError, match="422"):
        ss.http_push(server, "tok")({"a": 1})
    assert len(Handler.log) == 1                       # no retry on a 4xx


def test_http_push_refuses_to_send_the_token_over_plain_http():
    with pytest.raises(ValueError, match="plain http"):
        ss.http_push("http://beeroo.example.com", "tok")
    ss.http_push("https://beeroo.example.com", "tok")  # fine
