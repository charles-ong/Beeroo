import itertools
import json
import subprocess
import shutil
import sys
import threading
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))
import build_demo_db  # noqa: E402
import export_static  # noqa: E402

from app import queries  # noqa: E402
from common import db  # noqa: E402
from common.postcodes import state_for_postcode  # noqa: E402

ROOT = Path(__file__).parent.parent
NODE = shutil.which("node")


@pytest.fixture(scope="module")
def site(tmp_path_factory):
    base = tmp_path_factory.mktemp("static")
    path = base / "demo.sqlite3"
    conn = db.connect(str(path))
    build_demo_db.load_real(conn, raw=False)
    build_demo_db.simulate_history(conn)
    db.set_meta(conn, "demo", "1")
    out = base / "site"
    stats = export_static.export(conn, out, ["NSW", "ACT", "VIC", "WA", "QLD"])
    export_static.copy_site(out)
    return {"conn": conn, "out": out, "stats": stats}


# ---- structure --------------------------------------------------------------


def test_export_writes_manifest_variants_and_details(site):
    out, stats = site["out"], site["stats"]
    manifest = json.loads((out / "data" / "manifest.json").read_text())
    assert manifest["demo"] is True and set(manifest["states"]) == {"NSW", "ACT", "VIC", "WA", "QLD"}
    for state in manifest["states"]:
        for member in (0, 1):
            for pack in ("any", "single", "pack", "case"):
                assert (out / "data" / state / f"{member}-{pack}.json").exists()
    assert stats["files"] > 100
    assert stats["bytes"] / 1e6 < 30


def test_every_listed_product_has_a_detail_file_matching_the_server(site):
    out, conn = site["out"], site["conn"]
    variant = json.loads((out / "data" / "ACT" / "1-any.json").read_text())
    assert variant["products"]
    for p in variant["products"][:15]:
        detail = json.loads((out / "data" / "ACT" / "p" / f"{p['id']}.json").read_text())
        expected = queries.product_detail(conn, p["id"], "2600")
        assert detail["product"]["id"] == p["id"]
        assert detail["history"] == json.loads(json.dumps(expected["history"]))


def test_index_is_static_mode_with_relative_assets(site):
    html = (site["out"] / "index.html").read_text()
    assert 'name="beeroo-mode" content="static"' in html
    assert 'src="static/staticdata.js"' in html and 'src="static/app.js"' in html
    assert 'href="static/style.css"' in html
    assert '"/static/' not in html
    assert (site["out"] / ".nojekyll").exists()
    for name in ("app.js", "staticdata.js", "style.css"):
        assert (site["out"] / "static" / name).exists()


def test_server_index_uses_the_same_relative_paths():
    from fastapi.testclient import TestClient
    from app.main import create_app
    html = TestClient(create_app(":memory:")).get("/").text
    assert 'src="static/staticdata.js"' in html and 'name="beeroo-mode"' not in html


def test_states_without_any_data_are_not_exported(tmp_path):
    conn = db.connect(str(tmp_path / "empty.sqlite3"))
    stats = export_static.export(conn, tmp_path / "s", ["NSW", "WA"])
    assert stats["states"] == []


# ---- browser logic matches the server -----------------------------------------

CASES = []
for (sort, pack, member, q, minr, abv) in itertools.product(
    ["value", "unit_price", "abv", "name"], [None, "case"], [False, True], ["", "carlton dry"], [1, 2], [(None, None), (4.0, 5.2)],
):
    CASES.append({"sort": sort, "pack": pack, "member": member, "q": q, "min_retailers": minr, "abv": abv})


def test_browser_side_filtering_and_sorting_matches_the_server(site):
    if NODE is None:
        pytest.skip("node not installed")
    cases, expected = [], {}
    for i, c in enumerate(CASES):
        for postcode in ("2606", "6000", "2000"):
            name = f"{i}:{postcode}"
            cases.append({
                "name": name, "postcode": postcode, "pack": c["pack"], "include_member": c["member"],
                "query": {"q": c["q"], "sort": c["sort"], "min_retailers": c["min_retailers"],
                          "min_abv": c["abv"][0], "max_abv": c["abv"][1]},
            })
            r = queries.compare(site["conn"], postcode, q=c["q"], sort=c["sort"], pack_type=c["pack"],
                                include_member=c["member"], min_retailers=c["min_retailers"],
                                min_abv=c["abv"][0], max_abv=c["abv"][1], limit=1000)
            expected[name] = (r["meta"]["total"], [p["id"] for p in r["products"]])

    cases_file = site["out"].parent / "cases.json"
    cases_file.write_text(json.dumps(cases))
    run = subprocess.run([NODE, str(ROOT / "tests/js/static_parity.cjs"), str(site["out"]), str(cases_file)],
                         capture_output=True, text=True)
    assert run.returncode == 0, run.stderr
    got = {r["name"]: (r["total"], r["ids"]) for r in json.loads(run.stdout)["out"]}
    mismatches = [n for n in expected if got[n] != expected[n]]
    assert not mismatches, mismatches[:5]
    assert any(t for t, _ in expected.values())          # not vacuous
    assert len({tuple(ids) for _, ids in expected.values()}) > 10   # sorting actually varies


def test_browser_side_postcode_to_state_matches_python(site):
    if NODE is None:
        pytest.skip("node not installed")
    postcodes = ["0200", "0800", "0900", "1000", "2000", "2599", "2600", "2618", "2619", "2620", "2898", "2900", "2920",
                 "2921", "3000", "3999", "4000", "5000", "6000", "6797", "6798", "6800", "7000", "8000", "9000",
                 "0100", "abc", "123", "12345", "", "2606"]
    cases_file = site["out"].parent / "none.json"
    cases_file.write_text("[]")
    run = subprocess.run([NODE, str(ROOT / "tests/js/static_parity.cjs"), str(site["out"]), str(cases_file),
                          json.dumps(postcodes)], capture_output=True, text=True)
    assert [state_for_postcode(p) for p in postcodes] == json.loads(run.stdout)["states"]


# ---- real browser -----------------------------------------------------------

CHROMIUM = Path.home() / "Library/Caches/ms-playwright/chromium_headless_shell-1243/chrome-headless-shell-mac-arm64/chrome-headless-shell"


@pytest.mark.skipif(not CHROMIUM.exists(), reason="cached headless Chromium not available")
def test_exported_site_works_in_a_real_browser(site):
    pw = pytest.importorskip("playwright.sync_api")
    handler = partial(SimpleHTTPRequestHandler, directory=str(site["out"]))
    handler.log_message = lambda *a, **k: None
    srv = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{srv.server_port}"
    try:
        with pw.sync_playwright() as p:
            browser = p.chromium.launch(headless=True, executable_path=str(CHROMIUM))
            page = browser.new_page(viewport={"width": 1100, "height": 900})
            problems = []
            page.on("console", lambda m: problems.append(m.text) if m.type in ("error", "warning") else None)
            page.on("pageerror", lambda e: problems.append(str(e)))
            page.goto(f"{base}/?postcode=2606")
            page.wait_for_selector(".card")
            assert "Demo data" in page.inner_text("#demo-banner")
            page.fill("#q", "carlton dry lager bottles")
            page.wait_for_function("document.querySelectorAll('.card').length === 1")
            assert "$" in page.inner_text(".card")
            page.click(".card")
            page.wait_for_selector("#detail[open] svg circle")
            assert page.locator("#detail svg path").count() >= 1
            page.keyboard.press("Escape")
            # a state we don't cover gives a clear message, not a blank page
            page.fill("#postcode", "0800")
            page.dispatch_event("#postcode-form", "submit")
            page.wait_for_function("document.getElementById('postcode-error').textContent.includes('only has prices for')")
            page.fill("#postcode", "12")
            page.dispatch_event("#postcode-form", "submit")
            assert "valid 4-digit" in page.inner_text("#postcode-error")
            browser.close()
        assert problems == [], problems
    finally:
        srv.shutdown()
