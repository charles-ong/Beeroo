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
    stats = export_static.export(conn, out)
    export_static.copy_site(out)
    return {"conn": conn, "out": out, "stats": stats}


# ---- structure --------------------------------------------------------------


def test_export_writes_manifest_one_file_per_state_and_details(site):
    out, stats = site["out"], site["stats"]
    manifest = json.loads((out / "data" / "manifest.json").read_text())
    # only states with data of their own are exported: the demo data covers NSW (Dan Murphy's), ACT and WA
    assert manifest["demo"] is True and set(manifest["states"]) == {"NSW", "ACT", "WA"}
    assert [s["code"] for s in manifest["all_states"]] == ["ACT", "NSW", "NT", "QLD", "SA", "TAS", "VIC", "WA"]
    assert {s["code"]: s["postcode"] for s in manifest["all_states"]}["NSW"] == "2100"
    for state in manifest["states"]:
        assert (out / "data" / state / "products.json").exists()
        assert len(list((out / "data" / state).glob("*.json"))) == 1          # no per-filter variants any more
    assert stats["files"] > 100
    assert stats["bytes"] / 1e6 < 60


def test_every_listed_product_has_a_detail_file_matching_the_server(site):
    out, conn = site["out"], site["conn"]
    variant = json.loads((out / "data" / "ACT" / "products.json").read_text())
    assert variant["products"]
    for p in variant["products"][:15]:
        detail = json.loads((out / "data" / "ACT" / "p" / f"{p['id']}.json").read_text())
        expected = queries.product_detail(conn, p["id"], "ACT")
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


def test_nothing_is_exported_for_an_empty_database(tmp_path):
    conn = db.connect(str(tmp_path / "empty.sqlite3"))
    stats = export_static.export(conn, tmp_path / "s", ["NSW", "WA"])
    assert stats["states"] == []


# ---- browser logic matches the server -----------------------------------------

def make_cases():
    cases = []
    for sort, member, q, minr in itertools.product(["value", "unit_price", "abv", "rating", "name"], [False, True], ["", "carlton dry"], [1, 2]):
        cases.append({"sort": sort, "member": member, "q": q, "min_retailers": minr})
    # the new filters, alone and combined
    extra = [
        {"retailers": ["bws"]}, {"retailers": ["bws", "liquorland"]}, {"retailers": ["dan_murphys"]},
        {"types": ["Lager"]}, {"types": ["IPA", "Pale Ale"]}, {"types": ["Cider", "Ginger Beer"]},
        {"min_units": 24}, {"max_units": 6}, {"min_units": 4, "max_units": 12}, {"min_units": 24, "max_units": 24}, {"min_units": 40, "max_units": 30},
        {"min_abv": 4.0, "max_abv": 5.2}, {"min_abv": 3.5}, {"max_abv": 3.5},
        {"member": False, "min_units": 6, "types": ["Lager"], "retailers": ["liquorland"]},
        {"member": True, "min_units": 12, "max_units": 30, "min_abv": 4.5, "sort": "unit_price", "min_retailers": 2},
    ]
    for e in extra:
        cases.append({"sort": "value", "member": True, "q": "", "min_retailers": 1, **e})
    for sort in ("value", "unit_price", "abv", "rating", "name"):                   # and every sort the other way round
        cases.append({"sort": sort, "member": True, "q": "", "min_retailers": 1, "reverse": True})
        cases.append({"sort": sort, "member": False, "q": "", "min_retailers": 2, "reverse": True, "min_abv": 4.0})
    return cases


def query_of(c):
    return {"q": c["q"], "sort": c["sort"], "min_retailers": c["min_retailers"], "include_member": c["member"],
            "retailers": c.get("retailers"), "types": c.get("types"), "min_units": c.get("min_units"),
            "max_units": c.get("max_units"), "min_abv": c.get("min_abv"), "max_abv": c.get("max_abv"),
            "reverse": c.get("reverse", False)}


def digest(products):
    """The derived fields the cards show: they must match after the browser recomputes them."""
    return [[p["id"], p["best_value_retailer"], p["best_unit_price_retailer"],
             p["min_price_per_standard_drink"], p["min_unit_price"],
             [[k, len(e["options"]), e["last_updated"], e["stale"],
               e["best_value"] and e["best_value"]["price"], e["cheapest_unit"] and e["cheapest_unit"]["price"]]
              for k, e in sorted(p["retailers"].items())]] for p in products]


def test_browser_side_filtering_and_sorting_matches_the_server(site):
    if NODE is None:
        pytest.skip("node not installed")
    cases, expected = [], {}
    for i, c in enumerate(make_cases()):
        for state in ("ACT", "WA", "NSW"):
            name = f"{i}:{state}"
            query = query_of(c)
            cases.append({"name": name, "state": state, "query": query})
            r = queries.compare(site["conn"], state, q=query["q"], sort=query["sort"], include_member=query["include_member"],
                                retailers=query["retailers"], types=query["types"], min_units=query["min_units"],
                                max_units=query["max_units"], min_abv=query["min_abv"], max_abv=query["max_abv"],
                                min_retailers=query["min_retailers"], reverse=query["reverse"], limit=1000)
            expected[name] = (r["meta"]["total"], [p["id"] for p in r["products"]], digest(r["products"]))

    cases_file = site["out"].parent / "cases.json"
    cases_file.write_text(json.dumps(cases))
    run = subprocess.run([NODE, str(ROOT / "tests/js/static_parity.cjs"), str(site["out"]), str(cases_file)],
                         capture_output=True, text=True)
    assert run.returncode == 0, run.stderr
    got = {r["name"]: [r["total"], r["ids"], r["best"]] for r in json.loads(run.stdout)["out"]}
    mismatches = [n for n in expected if got[n] != json.loads(json.dumps(expected[n]))]
    assert not mismatches, mismatches[:5]
    assert any(t for t, *_ in expected.values())          # not vacuous
    assert len({tuple(ids) for _, ids, _ in expected.values()}) > 35   # the filters and sorting really vary the answers


def test_exported_payload_carries_the_facets_the_dropdowns_use(site):
    payload = json.loads((site["out"] / "data" / "ACT" / "products.json").read_text())
    facets = payload["meta"]["facets"]
    assert facets["units"] == sorted(facets["units"]) and facets["types"]
    assert all(set(p) >= {"name", "raw_name", "type"} for p in payload["products"])


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
            page.goto(f"{base}/")
            # the dropdown lists every state/territory by name; there is no postcode box
            page.wait_for_function("document.querySelectorAll('#state option').length === 9")
            names = page.eval_on_selector_all("#state option", "els => els.map(e => e.textContent)")
            assert names[0].startswith("Choose") and "New South Wales" in names                  # covered
            assert "Northern Territory (no prices yet)" in names and "Victoria (no prices yet)" in names   # not covered
            assert page.locator("#postcode").count() == 0
            page.select_option("#state", "ACT")
            page.wait_for_selector(".card")
            assert "Demo data" in page.inner_text("#demo-banner")
            assert "2600" in page.inner_text("#state-postcode")
            assert "Australian Capital Territory" in page.inner_text("#summary")
            page.fill("#q", "carlton dry lager bottles")
            page.wait_for_function("document.querySelectorAll('.card').length === 1")
            assert "$" in page.inner_text(".card")
            page.click(".card")
            page.wait_for_selector("#detail[open] svg circle")
            assert page.locator("#detail svg path").count() >= 1
            page.keyboard.press("Escape")
            # the choice is remembered and shareable in the URL
            assert page.evaluate("new URLSearchParams(location.search).get('state')") == "ACT"
            page.reload()
            page.wait_for_selector(".card")
            assert page.input_value("#state") == "ACT"
            # switching state changes the prices source
            page.select_option("#state", "WA")
            page.wait_for_function("document.getElementById('summary').textContent.includes('Western Australia')")
            # a state with no data explains itself instead of showing a blank page
            page.select_option("#state", "NT")
            page.wait_for_function("document.getElementById('state-error').textContent.includes('No prices for NT yet')")
            browser.close()
        assert problems == [], problems
    finally:
        srv.shutdown()


def test_a_state_without_its_own_data_is_not_exported_as_a_copy_of_another(tmp_path):
    """No 4,000-file copies of NSW under every other state; the dropdown says "no prices yet"."""
    conn = db.connect(str(tmp_path / "one.sqlite3"))
    sys.path.insert(0, str(ROOT / "scripts"))
    import ingest as ingest_script
    loc = ingest_script.bws.location_from_set_pickup(json.loads((ROOT / "tests/fixtures/bws_2606_set_pickup.json").read_text()))
    ingest_script.ingest_files(conn, "bws", [ROOT / "tests/fixtures/bws_2606_products.json"], loc, "2026-10-01T04:00:00+00:00")
    out = tmp_path / "site"
    stats = export_static.export(conn, out)
    manifest = json.loads((out / "data" / "manifest.json").read_text())
    assert manifest["states"] == ["ACT"] == stats["states"]                      # BWS Woden is ACT
    assert len(manifest["all_states"]) == 8                                      # but every state is still listed
    assert not (out / "data" / "NSW").exists() and not (out / "data" / "WA").exists()
    # inside the covered state, a retailer with no data is absent (and a notice says so), never faked
    act = json.loads((out / "data" / "ACT" / "products.json").read_text())
    assert act["locations"]["retailers"]["bws"]["store_name"] == "Woden"
    assert act["locations"]["retailers"]["liquorland"] is None
    assert "Liquorland prices for Australian Capital Territory" in " ".join(act["locations"]["notices"])
    assert all(set(p["retailers"]) == {"bws"} for p in act["products"])


@pytest.mark.skipif(not CHROMIUM.exists(), reason="cached headless Chromium not available")
def test_new_filters_work_in_a_real_browser(site):
    pw = pytest.importorskip("playwright.sync_api")
    handler = partial(SimpleHTTPRequestHandler, directory=str(site["out"]))
    handler.log_message = lambda *a, **k: None
    srv = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        with pw.sync_playwright() as p:
            browser = p.chromium.launch(headless=True, executable_path=str(CHROMIUM))
            page = browser.new_page(viewport={"width": 1100, "height": 900})
            problems = []
            page.on("console", lambda m: problems.append(m.text) if m.type in ("error", "warning") else None)
            page.on("pageerror", lambda e: problems.append(str(e)))
            page.goto(f"http://127.0.0.1:{srv.server_port}/")
            page.wait_for_function("document.querySelectorAll('#state option').length === 9")
            page.select_option("#state", "ACT")
            page.wait_for_selector(".card")
            count = lambda: int(page.evaluate("document.getElementById('summary').textContent.split(' ')[0]"))
            wait_count = lambda fn: page.wait_for_function(f"(() => {{ const n = parseInt(document.getElementById('summary').textContent); return {fn}; }})()")

            # member offers are on by default; the old pack dropdown and free-text ABV boxes are gone
            assert page.is_checked("#include_member")
            assert page.locator("#pack").count() == 0 and page.locator("input#min_abv, input#max_abv").count() == 0
            assert page.locator("select#min_abv").count() == 1 and page.locator("select#max_abv").count() == 1
            assert "member offer" in page.inner_text("#results")

            # titles carry no pack size or volume (the volume is shown on its own)
            titles = page.eval_on_selector_all(".card h2", "els => els.map(e => e.textContent)")
            assert titles and not any(__import__("re").search(r"\d\s*x\s*\d+\s*ml|\b\d+\s*ml\b", t, __import__("re").I) for t in titles)
            all_count = count()

            # retailers: several can be picked at once
            chips = page.locator(".chip")
            assert chips.count() == 3
            chips.nth(1).click()                                    # BWS
            wait_count(f"n < {all_count}")
            assert chips.nth(1).get_attribute("aria-pressed") == "true"
            only_bws = count()
            chips.nth(2).click()                                    # + Liquorland: now only products sold by BOTH
            wait_count(f"n < {only_bws}")
            both = count()
            assert 0 < both < only_bws
            chips.nth(0).click()                                    # + Dan Murphy's (ACT has none): nothing sells at all three
            wait_count("n === 0")
            chips.nth(0).click()
            chips.nth(1).click()                                    # BWS off again: Liquorland alone
            wait_count(f"n !== {both}")
            page.locator("#clear").click()
            wait_count(f"n === {all_count}")
            assert page.locator(".chip[aria-pressed='true']").count() == 0

            # type of beer
            kinds = page.eval_on_selector_all("#type-options input", "els => els.map(e => e.value)")
            assert "Lager" in kinds and len(kinds) > 3
            assert page.inner_text("#type-summary") == "All types" and not page.locator("#type[open]").count()
            page.click("#type summary")                                  # a dropdown of checkboxes: tick several
            page.wait_for_selector("#type[open] .opt")
            page.check("#type-options input[value='Lager']")
            wait_count(f"n < {all_count}")
            assert page.inner_text("#type-summary") == "Lager"
            assert set(page.eval_on_selector_all(".card .badge.kind", "els => els.map(e => e.textContent)")) == {"Lager"}
            only_lager = count()
            other = next(k for k in kinds if k != "Lager")
            page.check(f"#type-options input[value='{other}']")
            wait_count(f"n > {only_lager}")
            assert page.inner_text("#type-summary") == f"Lager, {other}"
            assert set(page.eval_on_selector_all(".card .badge.kind", "els => els.map(e => e.textContent)")) <= {"Lager", other}
            page.keyboard.press("Escape")                                # closes the list, keeps the choice
            assert not page.locator("#type[open]").count() and page.inner_text("#type-summary") == f"Lager, {other}"
            page.click("#type summary")
            page.click("#type-clear")
            wait_count(f"n === {all_count}")
            assert page.inner_text("#type-summary") == "All types"
            page.click("h1")                                             # clicking elsewhere closes it too
            assert not page.locator("#type[open]").count()

            # quantity range comes from the sizes that exist
            sizes = page.eval_on_selector_all("#min_units option", "els => els.map(e => e.value).filter(Boolean)")
            assert "1" in sizes and "24" in sizes
            page.select_option("#min_units", "24")
            page.select_option("#max_units", "24")
            wait_count(f"n < {all_count}")
            labels = page.eval_on_selector_all(".cell:not(.empty)", "els => els.map(e => e.children[2].textContent)")
            assert labels and all("Case of 24" in t for t in labels), labels[:3]

            # reverse order flips the list (and "Clear filters" puts it back)
            first = page.locator(".card h2").first.inner_text()
            page.select_option("#sort", "name")
            page.wait_for_function("document.querySelector('.card h2') && document.querySelector('.card h2').textContent !== " + json.dumps(first))
            a_to_z = page.locator(".card h2").first.inner_text()
            page.click("#reverse")
            assert page.get_attribute("#reverse", "aria-pressed") == "true"
            page.wait_for_function("document.querySelector('.card h2').textContent !== " + json.dumps(a_to_z))
            z_to_a = page.locator(".card h2").first.inner_text()
            assert z_to_a.lower() > a_to_z.lower()
            page.click("#clear")
            assert page.get_attribute("#reverse", "aria-pressed") == "false"
            page.wait_for_function("document.querySelector('.card h2').textContent === " + json.dumps(first))

            # ABV is a dropdown now
            page.select_option("#min_units", "")
            page.select_option("#max_units", "")
            page.select_option("#min_abv", "5")
            page.wait_for_function("[...document.querySelectorAll('.card .badge:not(.kind)')].every(b => parseFloat(b.textContent) >= 5)")
            browser.close()
        assert problems == [], problems
    finally:
        srv.shutdown()


@pytest.mark.skipif(not CHROMIUM.exists(), reason="cached headless Chromium not available")
def test_reviews_show_on_cards_and_in_the_modal(site):
    pw = pytest.importorskip("playwright.sync_api")
    handler = partial(SimpleHTTPRequestHandler, directory=str(site["out"]))
    handler.log_message = lambda *a, **k: None
    srv = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        with pw.sync_playwright() as p:
            browser = p.chromium.launch(headless=True, executable_path=str(CHROMIUM))
            page = browser.new_page(viewport={"width": 1100, "height": 900})
            problems = []
            page.on("console", lambda m: problems.append(m.text) if m.type in ("error", "warning") else None)
            page.on("pageerror", lambda e: problems.append(str(e)))
            page.goto(f"http://127.0.0.1:{srv.server_port}/")
            page.wait_for_function("document.querySelectorAll('#state option').length === 9")
            page.select_option("#state", "ACT")
            page.wait_for_selector(".card")
            # the list: an average and a count on rated products, nothing on unrated ones
            assert page.locator(".card .rating").count() > 0
            rated = page.locator(".card", has=page.locator(".rating")).first
            import re as _re
            text = rated.locator(".rating").inner_text()
            average = _re.search(r"\d\.\d", text).group()
            assert "(" in text and 0 < float(average) <= 5
            assert rated.locator(".stars .fill").count() == 1
            width = rated.locator(".stars .fill").evaluate("e => e.style.width")
            assert width.endswith("%") and 0 < float(width[:-1]) <= 100
            assert page.locator(".card", has_not=page.locator(".rating")).count() > 0
            # the modal: the pooled average, then each retailer
            rated.click()
            page.wait_for_selector("#detail[open] .reviews")
            assert page.locator("#detail h3", has_text="Reviews").count() == 1
            assert page.locator("#detail .reviews .big .num").inner_text() == average
            rows = page.locator("#detail .reviews tbody tr")
            assert rows.count() == 3
            body = page.inner_text("#detail .reviews")
            assert "bws" in body.lower() and "liquorland" in body.lower() and "review" in body
            assert "pooled by number of reviews" in body
            page.keyboard.press("Escape")
            browser.close()
        assert problems == [], problems
    finally:
        srv.shutdown()


@pytest.mark.skipif(not CHROMIUM.exists(), reason="cached headless Chromium not available")
def test_the_scraper_and_the_manual_snippet_read_the_stars_from_real_cards():
    """Run both pieces of in-page JavaScript against 7 real saved cards: San Miguel shows 4 full stars and a 54.31% one."""
    import re
    from scrapers import dan_murphys
    pw = pytest.importorskip("playwright.sync_api")
    html = (ROOT / "tests/fixtures/dan_murphys_page_sample.html").read_text()
    doc = (ROOT / "docs/DAN_MURPHYS_MANUAL.md").read_text()
    snippet = re.search(r"```js\n\s*(copy\(.*?\))\n\s*```", doc, re.S).group(1)
    with pw.sync_playwright() as p:
        browser = p.chromium.launch(headless=True, executable_path=str(CHROMIUM))
        page = browser.new_page()
        page.set_content(html)
        scraped = page.evaluate(dan_murphys._CARDS_JS)
        page.evaluate("window.copy = (text) => { window.copied = text; }")
        page.evaluate(snippet)
        copied = json.loads(page.evaluate("window.copied"))["cards"]
        browser.close()
    assert len(scraped) == 7 and scraped == copied                     # the manual snippet and the scraper agree
    san_miguel = next(c for c in scraped if "587292" in c["href"])
    assert san_miguel["rating"] == 4.54
    assert all(c["rating"] is None or 0 <= c["rating"] <= 5 for c in scraped)
    from scrapers import dan_murphys_dom as dom
    products, errors = dom.parse_cards_payload({"cards": scraped}, "k")
    got = {p.listing.retailer_sku: (p.listing.rating, p.listing.review_count) for p in products}
    assert got["587292"] == (4.54, 116) and errors == []


@pytest.mark.skipif(not CHROMIUM.exists(), reason="cached headless Chromium not available")
def test_prices_from_line_names_the_actual_stores(site, tmp_path):
    """The line under the banner names where each retailer's prices came from, not a placeholder."""
    import shutil as _shutil
    copy = tmp_path / "site"
    _shutil.copytree(site["out"], copy)
    path = copy / "data" / "ACT" / "products.json"
    payload = json.loads(path.read_text())
    stores = payload["locations"]["retailers"]
    stores["dan_murphys"] = {**(stores["dan_murphys"] or {"location_key": "dan_murphys:1462", "latest": "2026-10-08T14:45:00+00:00"}),
                             "store_name": "Canberra Airport", "suburb": "Majura", "state": "ACT", "postcode": "2609"}
    stores["bws"] = {**stores["bws"], "store_name": "Kingston", "suburb": "Kingston", "state": "ACT"}
    stores["liquorland"] = {**stores["liquorland"], "store_name": "Liquorland Woden", "suburb": None, "state": "ACT"}
    path.write_text(json.dumps(payload))

    pw = pytest.importorskip("playwright.sync_api")
    handler = partial(SimpleHTTPRequestHandler, directory=str(copy))
    handler.log_message = lambda *a, **k: None
    srv = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        with pw.sync_playwright() as p:
            browser = p.chromium.launch(headless=True, executable_path=str(CHROMIUM))
            page = browser.new_page()
            page.goto(f"http://127.0.0.1:{srv.server_port}/")
            page.wait_for_function("document.querySelectorAll('#state option').length === 9")
            page.select_option("#state", "ACT")
            page.wait_for_selector(".card")
            line = page.inner_text("#stores")
            assert line == ("Prices from — Dan Murphy's: Canberra Airport, Majura (ACT) · BWS: Kingston (ACT) · "
                            "Liquorland: Woden (ACT, state-wide prices)")
            # a state-level placeholder (no store known yet) is described as what it is
            stores["liquorland"]["store_name"] = "Liquorland ACT (state pricing)"
            path.write_text(json.dumps(payload))
            page.reload()
            page.select_option("#state", "ACT")
            page.wait_for_selector(".card")
            assert "Liquorland: state-wide pricing (ACT)" in page.inner_text("#stores")
            assert "imported by hand" not in page.inner_text("#stores")
            browser.close()
    finally:
        srv.shutdown()
