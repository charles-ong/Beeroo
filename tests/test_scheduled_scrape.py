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

from scrapers import bws as bws_mod  # noqa: E402
from scrapers import liquorland as ll_mod  # noqa: E402
from scrapers.bws_live import RobotsDisallow  # noqa: E402
from common.states import STATES  # noqa: E402

DM_PAGE = json.loads((F / "dan_murphys_browse_page1.json").read_text())
BWS_PAYLOAD = bws_mod.minimal_payload(json.loads((F / "bws_2606_products.json").read_text()))
LL_PAYLOAD = ll_mod.minimal_payload(json.loads((F / "liquorland_act_products.json").read_text()))
DM_STORE = Location(retailer=Retailer.DAN_MURPHYS, store_id="1546", store_name="Thornleigh",
                    suburb="Thornleigh", state="NSW", postcode="2120")
BWS_STORE = Location(retailer=Retailer.BWS, store_id="6723", store_name="Woden",
                     suburb="Woden", state="ACT", postcode="2606")
LL_STORE = ll_mod.location_for_site("ll_act")
STORES = {"dan_murphys": DM_STORE, "bws": BWS_STORE, "liquorland": LL_STORE}
PAGES = {"dan_murphys": DM_PAGE, "bws": BWS_PAYLOAD, "liquorland": LL_PAYLOAD}
ALL = ("bws", "liquorland", "dan_murphys")


class Clock:
    """Fake monotonic clock: sessions take `session_s`, sleeping advances time."""

    def __init__(self, session_s=300):
        self.t, self.session_s, self.slept = 0.0, session_s, []

    def monotonic(self):
        return self.t

    def sleep(self, seconds):
        self.slept.append(seconds)
        self.t += seconds


def fake(name, total=24, collected=None, pages=1, errors=(), calls=None, clock=None, products=None):
    async def scrape(postcode, max_pages):
        if calls is not None:
            calls.append((name, postcode, max_pages))
        if clock is not None:
            clock.t += clock.session_s
        n = products if products is not None else (total or 24)
        result = {"location": STORES[name], "raw_pages": [PAGES[name]] * pages,
                  "products": [object()] * n, "errors": list(errors), "total": total}
        if collected is not None:
            result["collected"] = collected
        return result
    return scrape


def blocked(name, calls, after=0):
    """Raises Blocked on the (after+1)th call; fine before that."""
    state = {"n": 0}

    async def scrape(postcode, max_pages):
        calls.append((name, postcode))
        state["n"] += 1
        if state["n"] > after:
            raise Blocked("bot protection page served (title='Attention Required! | Cloudflare')")
        return await fake(name)(postcode, max_pages)
    return scrape


def robots(name, calls):
    async def scrape(postcode, max_pages):
        calls.append((name, postcode))
        raise RobotsDisallow("robots.txt disallows /beer/all-beer")
    return scrape


@pytest.fixture()
def state(tmp_path):
    return str(tmp_path / "state.json")


def go(state, scrapes, push=None, now=NOW, clock=None, **kw):
    pushed = []
    push = push or (lambda body: pushed.append(body) or {"products": 24, "new_observations": 58, "new_listings": 24})
    clock = clock or Clock()
    kw.setdefault("min_spacing", 0)
    code, summary = ss.run(state_path=state, scrapes=scrapes, push=push, now=now,
                           sleep=clock.sleep, monotonic=clock.monotonic, **kw)
    return code, summary["results"], pushed


def by(results, name, zone=None):
    return [r for r in results if r["retailer"] == name and (zone is None or r.get("zone") == zone)]


# ---- states, not postcodes --------------------------------------------------


def test_every_state_and_territory_is_a_zone_with_its_pricing_postcode():
    assert ss.ZONES == list(STATES) == ["NSW", "ACT", "VIC", "QLD", "SA", "WA", "TAS", "NT"]
    assert STATES["NSW"]["postcode"] == "2100"                      # as specified
    assert all(len(v["postcode"]) == 4 for v in STATES.values())


def test_all_three_retailers_are_scheduled_in_a_fixed_order():
    assert ss.RETAILERS == ALL
    assert set(ss.KINDS) == set(ALL) and ss.KINDS["liquorland"] == "liquorland_products"


def test_zone_rotation_starts_in_order_then_least_recent():
    r = ss._blank()
    assert ss.pick_zone(r) == "NSW"
    r["zones"]["NSW"] = {"last_success": "2026-10-01"}
    assert ss.pick_zone(r) == "ACT"
    assert ss.pick_zones(r, n=3) == ["ACT", "VIC", "QLD"]
    r["zones"] = {z: {"last_success": f"2026-09-{10 + i}"} for i, z in enumerate(ss.ZONES)}
    assert ss.pick_zone(r) == "NSW"                  # oldest success first


def test_failed_attempts_do_not_count_as_fresh():
    r = ss._blank()
    r["zones"]["NSW"] = {"last_attempt": "2026-10-01", "last_error": "x"}
    assert ss.pick_zone(r) == "NSW"


def test_old_single_retailer_state_is_migrated(state):
    Path(state).write_text(json.dumps({"zones": {"2000": {"last_success": "2026-09-01"}},
                                       "blocked": {"consecutive": 2, "backoff_until": None}}))
    st = ss.load_state(state)
    assert st["retailers"]["dan_murphys"]["blocked"]["consecutive"] == 2
    assert "bws" not in st["retailers"]


def test_corrupt_state_file_is_treated_as_empty(state):
    Path(state).write_text("{not json")
    code, results, _ = go(state, {"bws": fake("bws")}, zones_per_run=1)
    assert code == 0 and results[0]["status"] == "ok"


# ---- a full daily run -------------------------------------------------------


def test_a_default_run_visits_every_state_at_every_retailer_interleaved(state):
    calls = []
    scrapes = {n: fake(n, calls=calls) for n in ALL}
    code, results, pushed = go(state, scrapes)
    assert code == 0 and len(calls) == 24 and len(pushed) == 24 and {r["status"] for r in results} == {"ok"}
    # NSW (2100) at all three sites first, then ACT (2600), and so on: no site is hit back to back
    assert [c[0] for c in calls[:6]] == ["bws", "liquorland", "dan_murphys"] * 2
    assert [c[1] for c in calls[:6]] == ["2100"] * 3 + ["2600"] * 3
    for name in ALL:
        assert [c[1] for c in calls if c[0] == name] == [STATES[z]["postcode"] for z in ss.ZONES]


def test_postcodes_passed_to_the_scrapers_come_from_the_states_table(state):
    calls = []
    go(state, {"bws": fake("bws", calls=calls)}, zone="WA")
    assert calls == [("bws", "6000", 60)]


def test_zones_per_run_limits_how_many_states_each_retailer_visits(state):
    calls = []
    go(state, {n: fake(n, calls=calls) for n in ALL}, zones_per_run=2)
    assert len(calls) == 6
    assert {c[1] for c in calls} == {"2100", "2600"}


def test_next_run_continues_with_the_least_recently_updated_states(state):
    calls = []
    scrapes = {"bws": fake("bws", calls=calls)}
    go(state, scrapes, zones_per_run=3)
    calls.clear()
    go(state, scrapes, zones_per_run=3, now=NOW + timedelta(days=1))
    assert [c[1] for c in calls] == [STATES[z]["postcode"] for z in ("QLD", "SA", "WA")]


def test_push_bodies_carry_kind_location_and_capture_time(state):
    _, _, pushed = go(state, {n: fake(n) for n in ALL}, zones_per_run=1)
    kinds = {b["kind"]: b for b in pushed}
    assert set(kinds) == {"bws_products", "liquorland_products", "dan_murphys_browse"}
    assert kinds["bws_products"]["payload"] == BWS_PAYLOAD and kinds["bws_products"]["observed_at"] == NOW.isoformat()
    assert kinds["bws_products"]["location"]["store_id"] == "6723"
    assert kinds["liquorland_products"]["location"] is None            # state-level; derived from the payload
    assert kinds["dan_murphys_browse"]["location"]["state"] == "NSW"


def test_retailer_filter_forced_state_and_jitter_once(state):
    clock, calls = Clock(), []
    go(state, {n: fake(n, calls=calls) for n in ALL}, retailers=["dan_murphys"], zone="NT", max_pages=3,
       jitter=600, clock=clock)
    assert calls == [("dan_murphys", "0800", 3)] and len(clock.slept) == 1 and 0 <= clock.slept[0] <= 600


# ---- politeness: spacing between sessions on the same site -----------------------


def test_a_single_site_is_slowed_down_between_states(state):
    clock = Clock(session_s=60)
    go(state, {"bws": fake("bws", clock=clock)}, zones_per_run=4, min_spacing=240, clock=clock)
    assert len(clock.slept) == 3 and all(180 <= s <= 300 for s in clock.slept)   # ~240 s +-25% between sessions on one site


def test_interleaving_already_spaces_sites_so_no_extra_waiting(state):
    clock = Clock(session_s=400)                       # three 400 s sessions between visits to the same site
    go(state, {n: fake(n, clock=clock) for n in ALL}, zones_per_run=3, min_spacing=240, clock=clock)
    assert clock.slept == []


# ---- completeness -----------------------------------------------------------


def test_out_of_stock_items_count_as_accounted_for(state):
    benign = [(i, "no available online prices") for i in range(541)]
    _, results, _ = go(state, {"bws": fake("bws", total=745, products=204, errors=benign)}, zone="ACT")
    assert results[0]["status"] == "ok"
    assert "last_success" in ss.load_state(state)["retailers"]["bws"]["zones"]["ACT"]


def test_liquorland_counts_list_entries_not_merged_products(state):
    # 987 entries merge into 392 products; "collected" is in the site's unit
    _, results, _ = go(state, {"liquorland": fake("liquorland", total=987, products=392, collected=987)}, zone="NSW")
    assert results[0]["status"] == "ok" and results[0]["collected"] == 987
    _, partial, _ = go(state, {"liquorland": fake("liquorland", total=987, products=392, collected=500)}, zone="VIC")
    assert partial[0]["status"] == "partial"


def test_unexplained_shortfall_is_partial_and_not_fresh(state):
    real_errors = [(i, "KeyError: 'Price'") for i in range(10)]
    code, results, pushed = go(state, {"dan_murphys": fake("dan_murphys", total=400, products=120, errors=real_errors)}, zone="NSW")
    assert code == 0 and results[0]["status"] == "partial" and pushed
    assert "last_success" not in ss.load_state(state)["retailers"]["dan_murphys"]["zones"]["NSW"]


def test_unknown_total_counts_as_incomplete(state):
    _, results, _ = go(state, {"bws": fake("bws", total=None)}, zone="NSW")
    assert results[0]["status"] == "partial"


# ---- blocking and backoff ---------------------------------------------------


def test_a_block_stops_only_that_retailer_for_the_rest_of_the_run(state):
    calls = []
    scrapes = {"bws": fake("bws", calls=calls), "liquorland": fake("liquorland", calls=calls),
               "dan_murphys": blocked("dan_murphys", calls)}
    code, results, pushed = go(state, scrapes)
    assert code == 3
    dm = by(results, "dan_murphys")
    assert len(dm) == 1 and dm[0]["status"] == "blocked" and dm[0]["backoff_days"] == 2      # asked once, then left alone
    assert len(by(results, "bws")) == 8 and len(by(results, "liquorland")) == 8              # others did every state
    assert len(pushed) == 16
    st = ss.load_state(state)["retailers"]
    assert datetime.fromisoformat(st["dan_murphys"]["blocked"]["backoff_until"]) == NOW + timedelta(days=2)
    assert st["bws"]["blocked"]["backoff_until"] is None


def test_a_block_part_way_through_keeps_what_was_collected(state):
    calls = []
    code, results, pushed = go(state, {"liquorland": blocked("liquorland", calls, after=2)})
    statuses = [r["status"] for r in results]
    assert statuses == ["ok", "ok", "blocked"] and code == 3 and len(pushed) == 2
    assert len(calls) == 3                                                  # never retried


def test_blocked_site_is_skipped_inside_its_backoff_window_then_retried(state):
    calls = []
    scrapes = {"bws": fake("bws", calls=calls), "dan_murphys": blocked("dan_murphys", calls)}
    go(state, scrapes, zones_per_run=1)
    calls.clear()
    code, results, _ = go(state, scrapes, zones_per_run=1, now=NOW + timedelta(days=1))
    assert [c[0] for c in calls] == ["bws"] and by(results, "dan_murphys")[0]["status"] == "backing_off"
    calls.clear()
    code, results, _ = go(state, scrapes, zones_per_run=1, now=NOW + timedelta(days=3))
    assert code == 3 and by(results, "dan_murphys")[0]["backoff_days"] == 4


def test_backoff_is_capped_and_success_resets_it(state):
    ss.save_state(state, {"retailers": {"dan_murphys": {"zones": {}, "blocked": {"consecutive": 9, "backoff_until": None}}}})
    _, results, _ = go(state, {"dan_murphys": blocked("dan_murphys", [])}, zones_per_run=1)
    assert results[0]["backoff_days"] == ss.MAX_BACKOFF_DAYS
    go(state, {"dan_murphys": fake("dan_murphys")}, zones_per_run=1, now=NOW + timedelta(days=20))
    assert ss.load_state(state)["retailers"]["dan_murphys"]["blocked"] == {"consecutive": 0, "backoff_until": None}


def test_robots_disallow_skips_the_retailer_for_a_week_without_counting_as_a_block(state):
    calls = []
    code, results, pushed = go(state, {"bws": robots("bws", calls)})
    assert code == 1 and results[0]["status"] == "robots_disallow" and pushed == [] and len(calls) == 1
    st = ss.load_state(state)["retailers"]["bws"]["blocked"]
    assert datetime.fromisoformat(st["backoff_until"]) == NOW + timedelta(days=7) and st["consecutive"] == 0
    assert go(state, {"bws": robots("bws", calls)}, now=NOW + timedelta(days=2))[1][0]["status"] == "backing_off"


# ---- other failures ---------------------------------------------------------


def test_scrape_error_is_recorded_without_backoff_and_other_retailers_still_run(state):
    async def boom(postcode, max_pages):
        raise RuntimeError("could not determine the active store")
    code, results, pushed = go(state, {"bws": boom, "dan_murphys": fake("dan_murphys")}, zones_per_run=2)
    assert code == 1 and [r["status"] for r in by(results, "bws")] == ["error", "error"]   # each state gets its try
    assert [r["status"] for r in by(results, "dan_murphys")] == ["ok", "ok"]
    st = ss.load_state(state)["retailers"]["bws"]
    assert "active store" in st["zones"]["NSW"]["last_error"] and st["blocked"]["backoff_until"] is None


def test_a_retailer_is_dropped_for_the_day_after_three_failures_in_a_row(state):
    async def boom(postcode, max_pages):
        raise RuntimeError("odd page")
    _, results, _ = go(state, {"bws": boom}, zones_per_run=6)
    assert [r["status"] for r in by(results, "bws")] == ["error"] * ss.MAX_CONSECUTIVE_FAILURES


def test_one_bad_state_does_not_cost_the_others_their_day(state):
    seen = []

    async def flaky(postcode, max_pages):
        seen.append(postcode)
        if len(seen) == 1:
            raise RuntimeError("odd store")
        return await fake("bws")(postcode, max_pages)
    _, results, _ = go(state, {"bws": flaky}, zones_per_run=3)
    assert [r["status"] for r in by(results, "bws")] == ["error", "ok", "ok"]


def test_push_failure_is_an_error_and_not_a_success(state):
    def bad(body):
        raise RuntimeError("could not reach server")
    code, results, _ = go(state, {"bws": fake("bws")}, push=bad, zone="NSW")
    assert code == 1 and results[0]["status"] == "push_failed"
    assert "last_success" not in ss.load_state(state)["retailers"]["bws"]["zones"]["NSW"]


# ---- the Liquorland switch ----------------------------------------------------


def test_skip_list_is_read_from_the_environment():
    assert ss.skipped_retailers({"BEEROO_SKIP_RETAILERS": "liquorland, bws"}) == {"liquorland", "bws"}
    assert ss.skipped_retailers({}) == set() and ss.skipped_retailers({"BEEROO_SKIP_RETAILERS": " ,"}) == set()


def test_cli_skips_the_retailers_named_in_the_environment(tmp_path, monkeypatch, capsys):
    calls = []
    monkeypatch.setattr(ss, "DEFAULT_LOG", tmp_path / "scrape.log")
    monkeypatch.setattr(ss, "real_scrapes", lambda browser_path=None: {n: fake(n, calls=calls) for n in ALL})
    monkeypatch.setenv("BEEROO_SKIP_RETAILERS", "liquorland")
    code = ss.main(["--db", str(tmp_path / "x.sqlite3"), "--state", str(tmp_path / "s.json"),
                    "--zones-per-run", "1", "--min-spacing", "0"])
    assert code == 0 and {c[0] for c in calls} == {"bws", "dan_murphys"}


def test_cli_includes_liquorland_by_default_and_all_states(tmp_path, monkeypatch, capsys):
    calls = []
    monkeypatch.setattr(ss, "DEFAULT_LOG", tmp_path / "scrape.log")
    monkeypatch.setattr(ss, "real_scrapes", lambda browser_path=None: {n: fake(n, calls=calls) for n in ALL})
    monkeypatch.delenv("BEEROO_SKIP_RETAILERS", raising=False)
    code = ss.main(["--db", str(tmp_path / "x.sqlite3"), "--state", str(tmp_path / "s.json"), "--min-spacing", "0"])
    assert code == 0 and len(calls) == 24 and {c[0] for c in calls} == set(ALL)


def test_cli_rejects_unknown_retailers_and_states():
    with pytest.raises(SystemExit):
        ss.main(["--retailer", "woolworths"])
    with pytest.raises(SystemExit):
        ss.main(["--zone", "2606"])


# ---- end to end -------------------------------------------------------------


def test_end_to_end_into_the_real_app_for_all_three_retailers(state, tmp_path, monkeypatch):
    monkeypatch.setenv("BEEROO_ADMIN_TOKEN", "t" * 32)
    client = TestClient(create_app(str(tmp_path / "srv.sqlite3")))

    def push(body):
        r = client.post("/api/admin/ingest", json=body, headers={"X-Admin-Token": "t" * 32})
        assert r.status_code == 200, r.text
        return r.json()

    scrapes = {"bws": fake("bws", total=16, products=14), "liquorland": fake("liquorland", total=60, products=40, collected=60),
               "dan_murphys": fake("dan_murphys")}
    code, results, _ = go(state, scrapes, push=push, zones_per_run=1)
    assert code == 0 and {r["status"] for r in results} == {"ok"}
    act = client.get("/api/compare", params={"state": "ACT"}).json()
    assert act["locations"]["retailers"]["bws"]["store_name"] == "Woden"
    assert act["locations"]["retailers"]["liquorland"]["location_key"] == "liquorland:ll_act"
    assert act["meta"]["total"] >= 14
    nsw = client.get("/api/compare", params={"state": "NSW"}).json()
    assert nsw["locations"]["retailers"]["dan_murphys"]["store_name"] == "Thornleigh"


def test_local_push_writes_to_a_database(tmp_path):
    path = tmp_path / "local.sqlite3"
    r = ss.local_push(path)({"kind": "dan_murphys_browse", "location": ss.location_dict(DM_STORE),
                             "payload": DM_PAGE, "observed_at": NOW.isoformat()})
    assert r["products"] == 24
    assert db.connect(str(path)).execute("SELECT COUNT(*) c FROM listings").fetchone()["c"] == 24


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


# ---- finding a browser when Playwright's pinned build isn't installed ---------------


def make_chromium(root, rev, mac=True):
    d = root / f"chromium-{rev}"
    exe = (d / "chrome-mac-arm64/Google Chrome for Testing.app/Contents/MacOS/Google Chrome for Testing") if mac else (d / "chrome-linux/chrome")
    exe.parent.mkdir(parents=True)
    exe.write_text("")
    return str(exe)


def test_discover_picks_the_newest_installed_chromium(tmp_path):
    make_chromium(tmp_path, 1200)
    newest = make_chromium(tmp_path, 1243)
    (tmp_path / "chromium_headless_shell-1300").mkdir()          # headless shells are not usable for headed runs
    (tmp_path / "firefox-1543").mkdir()
    assert ss.discover_chromium(tmp_path) == newest


def test_discover_handles_linux_layout_and_nothing_installed(tmp_path):
    linux = make_chromium(tmp_path, 1100, mac=False)
    assert ss.discover_chromium(tmp_path) == linux
    assert ss.discover_chromium(tmp_path / "empty") is None


def test_numeric_revisions_sort_numerically_not_alphabetically(tmp_path):
    make_chromium(tmp_path, 999)
    big = make_chromium(tmp_path, 1243)
    assert ss.discover_chromium(tmp_path) == big


def test_browser_resolution_order(tmp_path):
    cached = make_chromium(tmp_path / "cache", 1243)
    pinned = tmp_path / "pinned"
    pinned.write_text("")
    explicit = tmp_path / "mine"
    explicit.write_text("")
    # explicit path wins; Playwright's own build is used when it exists; else the cache; else a clear error
    assert ss.resolve_browser_path(str(explicit), str(pinned), tmp_path / "cache") == str(explicit)
    assert ss.resolve_browser_path(None, str(pinned), tmp_path / "cache") is None
    assert ss.resolve_browser_path(None, str(tmp_path / "missing"), tmp_path / "cache") == cached
    with pytest.raises(ss.NoBrowser, match="playwright install chromium"):
        ss.resolve_browser_path(None, str(tmp_path / "missing"), tmp_path / "nothing")
    with pytest.raises(ss.NoBrowser, match="does not exist"):
        ss.resolve_browser_path(str(tmp_path / "typo"), str(pinned), tmp_path / "cache")


# ---- clearing a backoff ------------------------------------------------------------


def test_clear_backoff_resets_only_the_named_retailer(state):
    go(state, {"dan_murphys": blocked("dan_murphys", []), "bws": robots("bws", [])})
    st = ss.load_state(state)["retailers"]
    assert st["dan_murphys"]["blocked"]["backoff_until"] and st["bws"]["blocked"]["backoff_until"]
    assert ss.clear_backoff(state, ["dan_murphys"]) == ["dan_murphys"]
    st = ss.load_state(state)["retailers"]
    assert st["dan_murphys"]["blocked"] == {"consecutive": 0, "backoff_until": None}
    assert st["bws"]["blocked"]["backoff_until"]                             # untouched
    assert ss.clear_backoff(state, ["dan_murphys"]) == []                    # nothing left to clear


def test_cli_clear_backoff_then_runs_that_retailer(tmp_path, monkeypatch):
    calls = []
    st = str(tmp_path / "s.json")
    # the CLI below uses the real clock, so the block must be recent in real time too (a fixed NOW goes stale)
    go(st, {"dan_murphys": blocked("dan_murphys", [])}, zones_per_run=1, now=datetime.now(timezone.utc))
    monkeypatch.setattr(ss, "DEFAULT_LOG", tmp_path / "scrape.log")
    monkeypatch.setattr(ss, "real_scrapes", lambda browser_path=None: {n: fake(n, calls=calls) for n in ALL})
    skipped = ss.main(["--db", str(tmp_path / "x.sqlite3"), "--state", st, "--retailer", "dan_murphys", "--zone", "NSW", "--min-spacing", "0"])
    assert calls == [] and skipped == 0                                      # still backing off: not even launched
    ran = ss.main(["--db", str(tmp_path / "x.sqlite3"), "--state", st, "--retailer", "dan_murphys", "--zone", "NSW",
                   "--clear-backoff", "dan_murphys", "--min-spacing", "0"])
    assert ran == 0 and [c[0] for c in calls] == ["dan_murphys"]


def test_cli_rejects_an_unknown_retailer_for_clear_backoff():
    with pytest.raises(SystemExit):
        ss.main(["--clear-backoff", "woolworths"])


# ---- a scrape can say which kind of page it hands over -------------------------------


def test_a_scrape_result_can_override_the_ingest_kind(state):
    base = fake("dan_murphys")

    async def cards(postcode, max_pages):
        return {**await base(postcode, max_pages), "kind": "dan_murphys_cards"}
    _, results, pushed = go(state, {"dan_murphys": cards}, zone="NSW")
    assert results[0]["status"] == "ok" and [b["kind"] for b in pushed] == ["dan_murphys_cards"]
    _, _, normal = go(state, {"dan_murphys": base}, zone="VIC")
    assert [b["kind"] for b in normal] == ["dan_murphys_browse"]
