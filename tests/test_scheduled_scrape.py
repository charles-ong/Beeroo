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
DM_PAGE = json.loads((F / "dan_murphys_browse_page1.json").read_text())
BWS_PAYLOAD = bws_minimal = None  # set below (needs scrapers.bws)

from scrapers import bws as bws_mod  # noqa: E402
from scrapers.bws_live import RobotsDisallow  # noqa: E402

BWS_PAYLOAD = bws_mod.minimal_payload(json.loads((F / "bws_2606_products.json").read_text()))
DM_STORE = Location(retailer=Retailer.DAN_MURPHYS, store_id="1546", store_name="Thornleigh",
                    suburb="Thornleigh", state="NSW", postcode="2120")
BWS_STORE = Location(retailer=Retailer.BWS, store_id="6723", store_name="Woden",
                     suburb="Woden", state="ACT", postcode="2606")
STORES = {"dan_murphys": DM_STORE, "bws": BWS_STORE}
PAGES = {"dan_murphys": DM_PAGE, "bws": BWS_PAYLOAD}


def fake(name, total=24, collected=24, pages=1, errors=(), calls=None):
    async def scrape(postcode, max_pages):
        if calls is not None:
            calls.append((name, postcode, max_pages))
        return {"location": STORES[name], "raw_pages": [PAGES[name]] * pages,
                "products": [object()] * collected, "errors": list(errors), "total": total}
    return scrape


def blocked(name, calls):
    async def scrape(postcode, max_pages):
        calls.append((name, postcode))
        raise Blocked("bot protection page served (title='Attention Required! | Cloudflare')")
    return scrape


def robots(name, calls):
    async def scrape(postcode, max_pages):
        calls.append((name, postcode))
        raise RobotsDisallow("robots.txt disallows /beer/all-beer")
    return scrape


@pytest.fixture()
def state(tmp_path):
    return str(tmp_path / "state.json")


def go(state, scrapes, push=None, now=NOW, **kw):
    pushed = []
    push = push or (lambda body: pushed.append(body) or {"products": 24, "new_observations": 58, "new_listings": 24})
    kw.setdefault("gap", 0)
    code, summary = ss.run(state_path=state, scrapes=scrapes, push=push, now=now, **kw)
    return code, summary["results"], pushed


def by(results, name):
    return next(r for r in results if r["retailer"] == name)


# ---- the four cities --------------------------------------------------------


def test_the_cities_are_sydney_canberra_melbourne_perth_only():
    assert ss.CITIES == {"2000": "Sydney NSW", "2600": "Canberra ACT", "3000": "Melbourne VIC", "6000": "Perth WA"}
    assert ss.ZONES == ["2000", "2600", "3000", "6000"]


def test_liquorland_is_never_automated():
    assert "liquorland" not in ss.RETAILERS and "liquorland" not in ss.KINDS
    with pytest.raises(SystemExit):
        ss.main(["--retailer", "liquorland"])


def test_zone_rotation_starts_in_order_then_least_recent():
    r = ss._blank()
    assert ss.pick_zone(r) == "2000"
    r["zones"]["2000"] = {"last_success": "2026-10-01"}
    assert ss.pick_zone(r) == "2600"
    assert ss.pick_zones(r, n=3) == ["2600", "3000", "6000"]
    r["zones"] = {z: {"last_success": f"2026-09-{10 + i}"} for i, z in enumerate(ss.ZONES)}
    assert ss.pick_zone(r) == "2000"                  # oldest success first


def test_failed_attempts_do_not_count_as_fresh():
    r = ss._blank()
    r["zones"]["2000"] = {"last_attempt": "2026-10-01", "last_error": "x"}
    assert ss.pick_zone(r) == "2000"


def test_old_single_retailer_state_is_migrated(state):
    Path(state).write_text(json.dumps({"zones": {"2000": {"last_success": "2026-09-01"}},
                                       "blocked": {"consecutive": 2, "backoff_until": None}}))
    st = ss.load_state(state)
    assert st["retailers"]["dan_murphys"]["zones"]["2000"]["last_success"] == "2026-09-01"
    assert st["retailers"]["dan_murphys"]["blocked"]["consecutive"] == 2
    assert "bws" not in st["retailers"]


def test_corrupt_state_file_is_treated_as_empty(state):
    Path(state).write_text("{not json")
    code, results, _ = go(state, {"bws": fake("bws")})
    assert code == 0 and results[0]["status"] == "ok"


# ---- running ----------------------------------------------------------------


def test_each_retailer_runs_one_city_by_default_and_rotates_independently(state):
    calls = []
    scrapes = {"dan_murphys": fake("dan_murphys", calls=calls), "bws": fake("bws", calls=calls)}
    code, results, pushed = go(state, scrapes)
    assert code == 0 and [c[0] for c in calls] == ["bws", "dan_murphys"]      # BWS first
    assert [c[1] for c in calls] == ["2000", "2000"] and len(pushed) == 2
    calls.clear()
    go(state, scrapes, now=NOW + timedelta(days=1))
    assert [c[1] for c in calls] == ["2600", "2600"]


def test_push_bodies_carry_kind_location_and_capture_time(state):
    _, _, pushed = go(state, {"bws": fake("bws"), "dan_murphys": fake("dan_murphys")})
    kinds = {b["kind"]: b for b in pushed}
    assert set(kinds) == {"bws_products", "dan_murphys_browse"}
    b = kinds["bws_products"]
    assert b["payload"] == BWS_PAYLOAD and b["observed_at"] == NOW.isoformat()
    assert b["location"] == {"store_id": "6723", "store_name": "Woden", "suburb": "Woden", "state": "ACT", "postcode": "2606"}


def test_all_four_cities_in_one_run_with_gaps_between_them(state):
    calls, slept = [], []
    code, results, _ = go(state, {"bws": fake("bws", calls=calls)}, zones_per_run=4, gap=240,
                          sleep=slept.append)
    assert [c[1] for c in calls] == ["2000", "2600", "3000", "6000"]
    assert len(slept) == 3 and all(180 <= s <= 300 for s in slept)               # +-25% around the gap
    st = ss.load_state(state)["retailers"]["bws"]["zones"]
    assert set(st) == set(ss.ZONES) and all(z["last_success"] for z in st.values())


def test_retailer_filter_forced_zone_and_jitter(state):
    slept, calls = [], []
    go(state, {"bws": fake("bws", calls=calls), "dan_murphys": fake("dan_murphys", calls=calls)},
       retailers=["dan_murphys"], zone="6000", max_pages=3, jitter=600, sleep=slept.append)
    assert calls == [("dan_murphys", "6000", 3)] and len(slept) == 1 and 0 <= slept[0] <= 600


# ---- completeness -----------------------------------------------------------


def test_out_of_stock_items_count_as_accounted_for(state):
    # BWS Woden: site lists 745, store stocks ~204; the rest are benign "no available online prices"
    benign = [(i, "no available online prices") for i in range(541)]
    _, results, _ = go(state, {"bws": fake("bws", total=745, collected=204, errors=benign)})
    assert results[0]["status"] == "ok"
    assert "last_success" in ss.load_state(state)["retailers"]["bws"]["zones"]["2000"]


def test_unexplained_shortfall_is_partial_and_not_fresh(state):
    real_errors = [(i, "KeyError: 'Price'") for i in range(10)]
    code, results, pushed = go(state, {"dan_murphys": fake("dan_murphys", total=400, collected=120, errors=real_errors)})
    assert code == 0 and results[0]["status"] == "partial" and pushed
    assert "last_success" not in ss.load_state(state)["retailers"]["dan_murphys"]["zones"]["2000"]


def test_unknown_total_counts_as_incomplete(state):
    _, results, _ = go(state, {"bws": fake("bws", total=None)})
    assert results[0]["status"] == "partial"


# ---- blocking and backoff ---------------------------------------------------


def test_a_block_stops_only_that_retailer_and_backs_off_exponentially(state):
    calls = []
    scrapes = {"bws": fake("bws", calls=calls), "dan_murphys": blocked("dan_murphys", calls)}
    code, results, pushed = go(state, scrapes)
    assert code == 3
    assert by(results, "dan_murphys")["status"] == "blocked" and by(results, "dan_murphys")["backoff_days"] == 2
    assert by(results, "bws")["status"] == "ok" and len(pushed) == 1            # BWS unaffected
    st = ss.load_state(state)["retailers"]
    assert datetime.fromisoformat(st["dan_murphys"]["blocked"]["backoff_until"]) == NOW + timedelta(days=2)
    assert st["bws"]["blocked"]["backoff_until"] is None

    # inside the window the blocked retailer is never launched; BWS still runs
    calls.clear()
    code, results, _ = go(state, scrapes, now=NOW + timedelta(days=1))
    assert [c[0] for c in calls] == ["bws"] and by(results, "dan_murphys")["status"] == "backing_off"

    # window over: tries again, blocked again -> 4 days
    calls.clear()
    code, results, _ = go(state, scrapes, now=NOW + timedelta(days=3))
    assert code == 3 and by(results, "dan_murphys")["backoff_days"] == 4


def test_a_block_ends_that_retailers_remaining_cities_for_the_day(state):
    calls = []
    go(state, {"dan_murphys": blocked("dan_murphys", calls)}, zones_per_run=4)
    assert len(calls) == 1


def test_backoff_is_capped(state):
    ss.save_state(state, {"retailers": {"dan_murphys": {"zones": {}, "blocked": {"consecutive": 9, "backoff_until": None}}}})
    _, results, _ = go(state, {"dan_murphys": blocked("dan_murphys", [])})
    assert results[0]["backoff_days"] == ss.MAX_BACKOFF_DAYS


def test_success_after_backoff_resets_the_counter(state):
    go(state, {"dan_murphys": blocked("dan_murphys", [])})
    go(state, {"dan_murphys": fake("dan_murphys")}, now=NOW + timedelta(days=3))
    assert ss.load_state(state)["retailers"]["dan_murphys"]["blocked"] == {"consecutive": 0, "backoff_until": None}


def test_robots_disallow_skips_the_retailer_for_a_week_without_counting_as_a_block(state):
    calls = []
    code, results, pushed = go(state, {"bws": robots("bws", calls)})
    assert code == 1 and results[0]["status"] == "robots_disallow" and pushed == []
    st = ss.load_state(state)["retailers"]["bws"]["blocked"]
    assert datetime.fromisoformat(st["backoff_until"]) == NOW + timedelta(days=7) and st["consecutive"] == 0
    assert go(state, {"bws": robots("bws", calls)}, now=NOW + timedelta(days=2))[1][0]["status"] == "backing_off"


# ---- other failures ---------------------------------------------------------


def test_scrape_error_is_recorded_without_backoff_and_other_retailers_still_run(state):
    async def boom(postcode, max_pages):
        raise RuntimeError("could not determine the active store")
    code, results, pushed = go(state, {"bws": boom, "dan_murphys": fake("dan_murphys")})
    assert code == 1 and by(results, "bws")["status"] == "error" and by(results, "dan_murphys")["status"] == "ok"
    st = ss.load_state(state)["retailers"]["bws"]
    assert "active store" in st["zones"]["2000"]["last_error"] and st["blocked"]["backoff_until"] is None


def test_push_failure_is_an_error_and_not_a_success(state):
    def bad(body):
        raise RuntimeError("could not reach server")
    code, results, _ = go(state, {"bws": fake("bws")}, push=bad)
    assert code == 1 and results[0]["status"] == "push_failed"
    assert "last_success" not in ss.load_state(state)["retailers"]["bws"]["zones"]["2000"]


# ---- end to end -------------------------------------------------------------


def test_end_to_end_into_the_real_app_for_both_retailers(state, tmp_path, monkeypatch):
    monkeypatch.setenv("BEEROO_ADMIN_TOKEN", "t" * 32)
    client = TestClient(create_app(str(tmp_path / "srv.sqlite3")))

    def push(body):
        r = client.post("/api/admin/ingest", json=body, headers={"X-Admin-Token": "t" * 32})
        assert r.status_code == 200, r.text
        return r.json()

    code, results, _ = go(state, {"bws": fake("bws", total=16, collected=14), "dan_murphys": fake("dan_murphys")}, push=push)
    assert code == 0 and {r["status"] for r in results} == {"ok"}
    act = client.get("/api/compare", params={"postcode": "2606"}).json()
    assert act["locations"]["retailers"]["bws"]["store_name"] == "Woden"
    assert act["meta"]["total"] >= 14
    nsw = client.get("/api/compare", params={"postcode": "2120"}).json()
    assert nsw["locations"]["retailers"]["dan_murphys"]["store_name"] == "Thornleigh"


def test_local_push_writes_to_a_database(tmp_path):
    path = tmp_path / "local.sqlite3"
    r = ss.local_push(path)({"kind": "dan_murphys_browse", "location": ss.location_dict(DM_STORE),
                             "payload": DM_PAGE, "observed_at": NOW.isoformat()})
    assert r["products"] == 24
    assert db.connect(str(path)).execute("SELECT COUNT(*) c FROM listings").fetchone()["c"] == 24


def test_cli_wiring_with_fake_browsers(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(ss, "DEFAULT_LOG", tmp_path / "scrape.log")
    monkeypatch.setattr(ss, "real_scrapes", lambda browser_path=None: {"bws": fake("bws"), "dan_murphys": fake("dan_murphys")})
    code = ss.main(["--db", str(tmp_path / "cli.sqlite3"), "--state", str(tmp_path / "cli_state.json"),
                    "--zones-per-run", "2", "--gap", "0", "--retailer", "bws"])
    out = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert code == 0 and [r["zone"] for r in out["results"]] == ["2000", "2600"]
    assert {r["retailer"] for r in out["results"]} == {"bws"}
    assert db.connect(str(tmp_path / "cli.sqlite3")).execute("SELECT COUNT(*) c FROM listings").fetchone()["c"] == 14


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
