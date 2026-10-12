import shutil
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))
import build_demo_db  # noqa: E402
import export_static  # noqa: E402
import send_alerts  # noqa: E402

from app import queries  # noqa: E402
from common import db  # noqa: E402
from common.alerts import Matcher, best_for_pack  # noqa: E402

ROOT = Path(__file__).parent.parent
NODE = shutil.which("node")


@pytest.fixture(scope="module")
def conn(tmp_path_factory):
    c = db.connect(str(tmp_path_factory.mktemp("alerts") / "demo.sqlite3"))
    build_demo_db.load_real(c, raw=False)
    build_demo_db.simulate_history(c)
    return c


def a_product(conn):
    locations = queries.resolve_locations(conn, "ACT")
    for p in queries.load_products(conn, locations):
        for r, e in p["retailers"].items():
            o = e["options"][0]
            return p, r, o
    raise AssertionError("demo data has no products")


def alert(p, o, **kw):
    return {"id": "a1", "email": "x@example.com", "product_id": p["id"], "state": "ACT", "token": "t" * 48,
            "pack": f"{o['pack_type']}:{o['units']}", "target": None, "include_member": True,
            "last_notified_price": None, **kw}


def test_target_fires_at_or_above_price_and_not_below(conn):
    p, r, o = a_product(conn)
    best = best_for_pack(p, f"{o['pack_type']}:{o['units']}")
    m = Matcher(conn)
    hit = m.check(alert(p, o, target=best["price"]))
    assert hit and hit["price"] == best["price"] and hit["product_name"] == p["name"]
    assert m.check(alert(p, o, target=best["price"] - 0.01)) is None


def test_not_repeated_for_same_price_but_for_a_lower_one(conn):
    p, r, o = a_product(conn)
    best = best_for_pack(p, f"{o['pack_type']}:{o['units']}")["price"]
    m = Matcher(conn)
    assert m.check(alert(p, o, target=best + 5, last_notified_price=best)) is None
    assert m.check(alert(p, o, target=best + 5, last_notified_price=best + 1)) is not None


def test_unknown_pack_or_product_never_fires(conn):
    p, r, o = a_product(conn)
    m = Matcher(conn)
    assert m.check(alert(p, o, pack="case:999", target=999)) is None
    assert m.check(alert(p, o, product_id=10**9, target=999)) is None


def test_member_offers_can_be_excluded(conn):
    locations = queries.resolve_locations(conn, "ACT")
    for p in queries.load_products(conn, locations):
        for e in p["retailers"].values():
            members = [o for o in e["options"] if o["member_only"]]
            if members:
                o = members[0]
                pack = f"{o['pack_type']}:{o['units']}"
                with_m = best_for_pack(p, pack, True)["price"]
                without = best_for_pack(p, pack, False)
                assert without is None or without["price"] >= with_m
                return
    pytest.skip("no member offers in the demo data")


def test_run_sends_marks_and_resets(conn):
    p, r, o = a_product(conn)
    best = best_for_pack(p, f"{o['pack_type']}:{o['units']}")["price"]
    fires = alert(p, o, id="fire", target=best + 1, email="a@example.com")
    quiet = alert(p, o, id="quiet", target=best - 1, email="b@example.com", last_notified_price=best)
    sent, calls = [], []

    def fetch(url, token=None, body=None, headers=None):
        calls.append((url, token, body))
        return {"alerts": [fires, quiet]} if url.endswith("/export") else {"ok": True}

    stats = send_alerts.run(conn, "https://x.test", "tok", lambda *a: sent.append(a), fetch=fetch)
    assert stats == {"alerts": 2, "sent": 1, "failed": 0, "reset": 1}
    assert len(sent) == 1 and sent[0][0] == "a@example.com"
    assert "https://x.test/api/alerts/unsubscribe?t=" + "t" * 48 in sent[0][2]
    mark = [c for c in calls if c[0].endswith("/mark")][0]
    assert mark[1] == "tok"
    assert mark[2]["updates"] == [{"id": "fire", "last_notified_price": best},
                                  {"id": "quiet", "last_notified_price": None}]


def test_dry_run_sends_and_marks_nothing(conn):
    p, r, o = a_product(conn)
    fires = alert(p, o, target=999)
    sent, calls = [], []
    stats = send_alerts.run(conn, "https://x.test", "tok", lambda *a: sent.append(a), dry_run=True,
                            fetch=lambda url, **kw: (calls.append(url), {"alerts": [fires]})[1])
    assert not sent and calls == ["https://x.test/api/alerts/export"] and stats["sent"] == 0


def test_a_failing_send_does_not_stop_others_or_get_marked(conn):
    p, r, o = a_product(conn)
    alerts = [alert(p, o, id="bad", target=999), alert(p, o, id="good", target=999, email="g@example.com")]
    seen = []

    def send(to, s, t):
        if to == "x@example.com":
            raise RuntimeError("boom")
        seen.append(to)

    marks = []
    stats = send_alerts.run(conn, "https://x.test", "tok", send,
                            fetch=lambda url, token=None, body=None, **kw: marks.append(body) or {"alerts": alerts})
    assert stats["sent"] == 1 and stats["failed"] == 1 and seen == ["g@example.com"]
    assert [u["id"] for u in marks[-1]["updates"]] == ["good"]


def test_unconfigured_script_skips(monkeypatch, capsys):
    for k in ("BEEROO_SITE_URL", "ALERTS_ADMIN_TOKEN", "RESEND_API_KEY", "ALERTS_FROM"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setattr(sys, "argv", ["send_alerts.py"])
    send_alerts.main()
    assert "skipping" in capsys.readouterr().out


def test_site_ships_sponsor_legal_pages_and_age_gate(conn, tmp_path):
    export_static.export(conn, tmp_path)
    export_static.copy_site(tmp_path)
    for name in ("sponsored.json", "terms.html", "privacy.html"):
        assert (tmp_path / name).exists()
    html = (tmp_path / "index.html").read_text()
    assert 'id="agegate"' in html and 'id="sponsored"' in html and "terms.html" in html


@pytest.mark.skipif(not NODE, reason="node not installed")
def test_alert_functions():
    out = subprocess.run([NODE, "--test", str(ROOT / "tests" / "js" / "alerts_functions.test.mjs")],
                         capture_output=True, text=True, cwd=ROOT)
    assert out.returncode == 0, out.stdout + out.stderr


from test_static import CHROMIUM, site  # noqa: E402,F401  (the exported demo site fixture and the cached Chromium)


@pytest.mark.skipif(not CHROMIUM.exists(), reason="cached headless Chromium not available")
def test_age_gate_sponsor_and_alert_form_in_a_real_browser(site):
    import threading
    from functools import partial
    from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
    pw = pytest.importorskip("playwright.sync_api")
    handler = partial(SimpleHTTPRequestHandler, directory=str(site["out"]))
    handler.log_message = lambda *a, **k: None
    srv = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{srv.server_port}"
    try:
        with pw.sync_playwright() as p:
            browser = p.chromium.launch(headless=True, executable_path=str(CHROMIUM))
            page = browser.new_context().new_page()
            problems = []
            page.on("pageerror", lambda e: problems.append(str(e)))
            page.route("**/api/alerts", lambda r: r.fulfill(status=200, content_type="application/json",
                                                           body='{"ok":true,"message":"Check your inbox."}'))
            page.goto(base + "/")
            page.wait_for_selector("#agegate[open]")                       # first visit: asked
            page.keyboard.press("Escape")
            assert page.locator("#agegate[open]").count() == 1             # Escape doesn't skip it
            page.click("#age-no")
            assert "Sorry" in page.inner_text("#agegate-title") and page.locator("#age-yes").is_hidden()
            page.reload()
            page.click("#age-yes")
            assert page.locator("#agegate[open]").count() == 0
            page.reload()
            page.wait_for_function("document.querySelectorAll('#state option').length === 9")
            assert page.locator("#agegate[open]").count() == 0             # remembered
            page.select_option("#state", "ACT")
            page.wait_for_selector(".card")
            sp = page.inner_text("#sponsored").lower()
            assert "sponsored" in sp and "example placement" in sp
            assert page.evaluate("document.getElementById('sponsored').compareDocumentPosition(document.getElementById('results')) & 4")  # above the list
            page.click(".card")
            page.wait_for_selector("#alert-email")
            page.fill("#alert-email", "me@example.com")
            page.click(".alertbox button[type=submit]")
            page.wait_for_selector(".alertbox .msg.ok")
            assert "Check your inbox" in page.inner_text(".alertbox .msg")
            assert not problems, problems
            browser.close()
    finally:
        srv.shutdown()
