"""The extension's header-text collector (content.js) against the REAL Dan
Murphy's page markup, in a real Chromium engine. Scripts are stripped from the
snapshot and all network is blocked, so nothing contacts the retailer."""
import json
from pathlib import Path

import pytest

playwright_sync = pytest.importorskip("playwright.sync_api")
from bs4 import BeautifulSoup  # noqa: E402

ROOT = Path(__file__).parent.parent
SNAPSHOT = ROOT / "tests" / "fixtures" / "dan_murphys_beer_listing.html"
CONTENT_JS = (ROOT / "extension" / "content.js").read_text()
CHROMIUM = Path.home() / "Library/Caches/ms-playwright/chromium_headless_shell-1243/chrome-headless-shell-mac-arm64/chrome-headless-shell"

pytestmark = pytest.mark.skipif(not CHROMIUM.exists(), reason="cached headless Chromium not available")

STUB = """
window.__msgs = [];
window.chrome = { runtime: { sendMessage: (m) => { window.__msgs.push(m); return Promise.resolve(); } } };
"""


def static_html(transform=None):
    soup = BeautifulSoup(SNAPSHOT.read_text(encoding="utf-8"), "html.parser")
    for tag in soup(["script", "iframe", "noscript"]):
        tag.decompose()
    for link in soup.find_all("link"):
        link.decompose()
    if transform:
        transform(soup)
    return str(soup)


@pytest.fixture(scope="module")
def browser():
    with playwright_sync.sync_playwright() as p:
        b = p.chromium.launch(headless=True, executable_path=str(CHROMIUM))
        yield b
        b.close()


def collected_texts(browser, html, scroll=0):
    page = browser.new_page(viewport={"width": 1440, "height": 900})

    def handle(route):
        # Serve the snapshot as the site's own page (so location.origin is realistic);
        # every other request is blocked. Nothing leaves this machine.
        if route.request.resource_type == "document":
            route.fulfill(status=200, content_type="text/html", body=html)
        else:
            route.abort()

    page.route("**/*", handle)
    page.add_init_script(STUB)
    page.goto("https://www.danmurphys.com.au/beer/all")
    page.evaluate(CONTENT_JS)
    if scroll:
        page.evaluate(f"window.scrollTo(0, {scroll})")
    page.evaluate("window.postMessage({__beeroo: 1, url: 'https://x/', method: 'GET', json: {}}, location.origin)")
    page.wait_for_function("window.__msgs.length > 0", timeout=5000)
    texts = page.evaluate("window.__msgs[0].texts")
    page.close()
    return texts


def test_logged_out_header_text_is_collected_from_the_real_markup(browser):
    texts = collected_texts(browser, static_html())
    assert "Login" in texts, texts[:40]


def test_a_logged_in_header_has_no_login_text(browser):
    def logged_in(soup):
        for el in soup.find_all(string=lambda s: s and s.strip() == "Login"):
            el.replace_with("Hi, Sam")

    texts = collected_texts(browser, static_html(logged_in))
    assert "Login" not in texts and not any(t.lower().startswith("login") for t in texts)


def test_texts_are_short_and_bounded(browser):
    texts = collected_texts(browser, static_html())
    assert 0 < len(texts) <= 80 and all(len(t) <= 40 for t in texts)


def test_the_check_works_end_to_end_with_the_real_markup(browser):
    """collector output -> looksLoggedOut, executed in Node."""
    import subprocess, shutil
    if shutil.which("node") is None:
        pytest.skip("node not installed")
    texts = collected_texts(browser, static_html())
    script = (
        "import { looksLoggedOut } from './extension/lib/loggedout.js';"
        f"console.log(looksLoggedOut({json.dumps(texts)}));"
    )
    out = subprocess.run(["node", "--input-type=module", "-e", script], cwd=ROOT, capture_output=True, text=True)
    assert out.stdout.strip() == "true", out.stderr
