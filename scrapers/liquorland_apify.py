"""Liquorland prices through a third-party Apify actor instead of our own browser.

Liquorland's bot protection (Imperva) rejects our own automation from GitHub's servers, so when APIFY_TOKEN is set the
scheduled scraper fetches the beer list through the community actor
`dromb/liquorland-au-catalog-product-lookup-unofficial` (https://apify.com/dromb/liquorland-au-catalog-product-lookup-unofficial).
That actor uses residential proxies and re-establishes a session when the site blocks it, i.e. it gets past the bot
protection we otherwise stop at. This was the operator's decision (docs/ACCESS_NOTES.md). It is paid per result
plus Apify platform usage, billed to the account that owns APIFY_TOKEN.

One actor run returns one list page, so a state is fetched as page 1 (which reports the page count) and then the
remaining pages a few at a time. The result has the same shape as scrapers/liquorland_live.scrape, so the rest of
the pipeline (scripts/scheduled_scrape.py, app/ingest.py) cannot tell the difference.

Only the standard library is used for HTTP. The token goes in an Authorization header, never in a URL.
"""
import json
import logging
import os
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from common.records import utcnow
from scrapers import liquorland

log = logging.getLogger("beeroo.liquorland_apify")

API = "https://api.apify.com/v2"
ACTOR = "dromb~liquorland-au-catalog-product-lookup-unofficial"
CATEGORY_PATH = "/beer-and-cider"        # the same list the live driver reads: /beer-and-cider with facets=beer
FACETS = "beer"
SHOW = 60                                # entries per page; the site's own API is known to accept 60
MEMORY_MB = 2048                         # the actor's README asks for at least 2048 MB
RUN_TIMEOUT_S = 600                      # per actor run
PARALLEL = 3                             # pages fetched at once (Apify's free plan allows 8 GB of runs at a time)
FINISHED = {"SUCCEEDED", "FAILED", "ABORTED", "TIMED-OUT"}
DEBUG_DIR = Path("data/failed")


class ApifyError(RuntimeError):
    pass


def _request(method, url, token, body=None, timeout=120):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method, headers={
        "Authorization": f"Bearer {token}", "Content-Type": "application/json", "Accept": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            text = resp.read().decode("utf-8")
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", "replace")[:300]
        raise ApifyError(f"Apify {method} {url.split('?')[0]} -> HTTP {e.code}: {detail}") from None
    except urllib.error.URLError as e:
        raise ApifyError(f"Apify {method} {url.split('?')[0]} failed: {e.reason}") from None
    if not text:
        return None
    try:
        return json.loads(text)
    except ValueError:
        raise ApifyError(f"Apify {method} {url.split('?')[0]} returned non-JSON: {text[:200]!r}") from None


def actor_input(state, page):
    return {
        "operation": "category", "path": CATEGORY_PATH, "facets": FACETS, "state": state.lower(),
        "page": page, "show": SHOW, "includeRaw": True,
        "proxyMode": "auto", "automaticSessionRecovery": True,
    }


def run_actor(token, run_input, request=_request, sleep=time.sleep, monotonic=time.monotonic):
    """Start one actor run, wait for it, and return (OUTPUT record, dataset items)."""
    started = request("POST", f"{API}/acts/{ACTOR}/runs?waitForFinish=60&memory={MEMORY_MB}&timeout={RUN_TIMEOUT_S}",
                      token, run_input)
    run = (started or {}).get("data") or {}
    deadline = monotonic() + RUN_TIMEOUT_S + 120
    while run.get("status") not in FINISHED:
        if not run.get("id"):
            raise ApifyError(f"Apify did not start a run: {started!r}"[:300])
        if monotonic() > deadline:
            raise ApifyError(f"actor run {run['id']} still {run.get('status')} after {RUN_TIMEOUT_S + 120}s")
        sleep(1)
        run = (request("GET", f"{API}/actor-runs/{run['id']}?waitForFinish=60", token) or {}).get("data") or {}

    output = None
    if run.get("defaultKeyValueStoreId"):
        try:
            output = request("GET", f"{API}/key-value-stores/{run['defaultKeyValueStoreId']}/records/OUTPUT", token)
        except ApifyError as e:
            if "HTTP 404" not in str(e):
                raise
    if run["status"] != "SUCCEEDED":
        why = run.get("statusMessage") or (output or {}).get("error") or (output or {}).get("message") or ""
        raise ApifyError(f"actor run {run.get('id')} ended {run['status']}: {why}"[:400])

    items = []
    if run.get("defaultDatasetId"):
        items = request("GET", f"{API}/datasets/{run['defaultDatasetId']}/items?clean=true&format=json", token) or []
    return output, items


def _looks_like_entry(x):
    return isinstance(x, dict) and "unitOfMeasure" in x and isinstance(x.get("price"), dict) and x.get("id")


def _find_raw_list(node, depth=0):
    """The site's own list response somewhere inside the actor's OUTPUT (includeRaw=true), or None."""
    if depth > 6:
        return None
    if isinstance(node, dict):
        products = node.get("products")
        if isinstance(products, list) and products and all(_looks_like_entry(p) for p in products[:5]):
            return node
        children = node.values()
    elif isinstance(node, list):
        children = node
    else:
        return None
    for child in children:
        found = _find_raw_list(child, depth + 1)
        if found is not None:
            return found
    return None


def _pagination(output, raw):
    for source in ((raw or {}).get("meta") or {}).get("page"), (output or {}).get("pagination"):
        if isinstance(source, dict) and source:
            return {k: source.get(k) for k in ("current", "total", "size", "productCount")}
    return {}


def to_page(output, items, state):
    """One list page in the shape scrapers/liquorland.py parses: {debugQuery, meta.page, products}."""
    site = f"ll_{state.lower()}"
    raw = _find_raw_list(output)
    if raw is not None:
        seen = liquorland.site_state(raw)
        if seen and seen != site:
            raise ApifyError(f"asked for {site} but the actor returned {seen}")
        page = {**raw, "debugQuery": raw.get("debugQuery") or f"sitestate={site}"}
    elif items and all(_looks_like_entry(i) for i in items[:5]):
        page = {"debugQuery": f"sitestate={site}", "products": items}      # normalised items kept the site's fields
    elif output and _pagination(output, None).get("productCount") == 0:
        page = {"debugQuery": f"sitestate={site}", "products": []}
    else:
        keep_sample(state, output, items)
        raise ApifyError("the actor's result has no recognisable Liquorland product list "
                         f"(sample kept in {DEBUG_DIR}/liquorland-apify-{state.lower()}.json)")
    page["meta"] = {"page": _pagination(output, raw)}
    return page


def keep_sample(state, output, items):
    try:
        DEBUG_DIR.mkdir(parents=True, exist_ok=True)
        (DEBUG_DIR / f"liquorland-apify-{state.lower()}.json").write_text(
            json.dumps({"output": output, "items": (items or [])[:3]}, indent=1)[:400_000])
    except OSError:
        log.warning("could not keep the actor's result", exc_info=True)


def fetch_state(state, token, max_pages=30, run=run_actor, parallel=PARALLEL):
    """All list pages for one state: [page payload, ...]."""
    first = to_page(*run(token, actor_input(state, 1)), state)
    pages_total = first["meta"]["page"].get("total") or 1
    numbers = list(range(2, min(int(pages_total), max_pages) + 1))
    log.info("liquorland %s via Apify: page 1 of %s (%s entries reported)", state, pages_total,
             first["meta"]["page"].get("productCount"))

    def one(n):
        return to_page(*run(token, actor_input(state, n)), state)

    with ThreadPoolExecutor(max_workers=max(1, parallel)) as pool:
        rest = list(pool.map(one, numbers))
    return [first, *rest]


def make_scrape(token, run=run_actor):
    """An async scrape(postcode, max_pages) for scripts/scheduled_scrape.py, like browser_scrape(liquorland_live.scrape)."""
    import asyncio
    from common.postcodes import state_for_postcode

    async def scrape(postcode, max_pages):
        state = state_for_postcode(postcode)
        site = f"ll_{state.lower()}"
        pages = await asyncio.to_thread(fetch_state, state, token, max_pages, run)

        seen, unique = set(), []
        for pg in pages:
            for p in pg.get("products") or []:
                if p.get("id") not in seen:
                    seen.add(p.get("id"))
                    unique.append(p)
        location = liquorland.location_for_site(site)
        products, errors = liquorland.parse_liquorland_payload(
            {"debugQuery": f"sitestate={site}", "products": unique}, location.location_key, utcnow())
        total = max((pg["meta"]["page"].get("productCount") or 0 for pg in pages), default=0) or len(unique)
        return {"location": location, "raw_pages": [liquorland.minimal_payload(pg) for pg in pages],
                "products": products, "errors": errors, "total": total, "collected": len(unique)}

    return scrape


def token_from_env(environ=os.environ):
    return environ.get("APIFY_TOKEN", "").strip() or None
