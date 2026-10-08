import asyncio
import copy
import json
import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.main import create_app
from common.records import Location, PackType, Retailer
from scrapers import dan_murphys, dan_murphys_dom as dom

F = Path(__file__).parent / "fixtures"
CARDS = json.loads((F / "dan_murphys_cards.json").read_text())      # real cards, 2026-09 snapshot
K = "dan_murphys:test"


def run(coro):
    return asyncio.run(coro)


def card(*lines, href="/product/DM_1/test-beer-330ml"):
    return {"href": href, "lines": list(lines)}


def parse(*lines, **kw):
    return dom.parse_card(card(*lines, **kw), K)


def options(p):
    return sorted((o.pack_type.value, o.units, o.price, o.member_only) for o in p.prices)


# ---- real cards ------------------------------------------------------------------


def test_every_real_card_parses():
    products, errors = dom.parse_cards_payload(CARDS, K)
    assert len(products) == 48 and errors == []
    assert all(p.listing.retailer == Retailer.DAN_MURPHYS and p.prices for p in products)


def by_name(name):
    products, _ = dom.parse_cards_payload(CARDS, K)
    return next(p for p in products if p.listing.name == name)


def test_case_and_pack_prices():
    p = by_name("San Miguel Pale Pilsen Bottles 330mL")
    assert options(p) == [("case", 24, 71.99, False), ("pack", 6, 25.99, False)]
    assert p.listing.retailer_sku == "587292"                       # DM_ prefix dropped, like the JSON path's Stockcode
    assert p.listing.url == "https://www.danmurphys.com.au/product/587292/san-miguel-pale-pilsen-bottles-330ml"
    assert (p.listing.brand, p.listing.unit_volume_ml) == ("San Miguel", 330)


def test_member_offer_keeps_both_prices_flagged():
    p = by_name("Hollandia Lager Bottles 330mL")
    assert options(p) == [("case", 24, 49.0, True), ("case", 24, 49.95, False)]


def test_multibuy_of_packs_is_sized_from_the_packs_own_count():
    p = by_name("Dab Original German Beer Cans 500mL")             # "$42 for 2 packs", pack (6)
    assert (PackType.CASE.value, 12, 42.0, True) in options(p)


def test_multibuy_of_cases():
    p = by_name("Heineken Lager Bottles 330mL")                      # "$109.90 for 2 cases", case (24)
    assert ("case", 48, 109.9, False) in options(p) or ("case", 48, 109.9, True) in options(p)


def test_in_store_only_prices_are_not_online_prices():
    p = by_name("Victoria Bitter Lager Cans 375mL")                  # "$5.99 each (in-store)"
    assert options(p) == [("case", 30, 67.95, False)]


def test_abv_comes_from_the_name_only_never_invented():
    assert by_name("Miller Dry 3.5% Cans 355mL").listing.abv == 3.5
    assert by_name("San Miguel Pale Pilsen Bottles 330mL").listing.abv is None


# ---- the two text layouts --------------------------------------------------------


def merged(lines):
    """inner_text() layout: "$71.99 case (24)" on one line, "$ 42" collapsed."""
    text = "\n".join(lines)
    text = re.sub(r"\$\n(\d)", r"$\1", text)
    text = re.sub(r"(\$[\d.]+)\n((?:case|pack|each|for )[^\n]*)", r"\1 \2", text)
    return text.split("\n")


def test_inner_text_layout_gives_identical_results():
    split = dom.parse_cards_payload(CARDS, K)[0]
    other = dom.parse_cards_payload({"cards": [{**c, "lines": merged(c["lines"])} for c in CARDS["cards"]]}, K)[0]
    assert [(p.listing.retailer_sku, options(p)) for p in split] == [(p.listing.retailer_sku, options(p)) for p in other]


def test_review_noise_and_buttons_are_not_part_of_the_name():
    p = parse("(116 REVIEWS)", "San Miguel", "Pale Pilsen Bottles 330mL", "$71.99 case (24)", "Add to cart", "Sponsored")
    assert p.listing.name == "San Miguel Pale Pilsen Bottles 330mL"


def test_each_price_uses_the_pack_count_in_the_title():
    p = parse("Heineken", "24 x 330mL Cans", "$49.99 each")
    assert options(p) == [("case", 24, 49.99, False)]
    assert options(parse("La Chouffe", "Ale 750mL", "$19.99 each")) == [("single", 1, 19.99, False)]


def test_a_single_name_line_is_still_a_name():
    assert parse("Foo Beer 330mL", "$20 pack (6)").listing.brand is None


# ---- never guess ------------------------------------------------------------------


@pytest.mark.parametrize("c,why", [
    (card("Foo", "Bar 330mL", "$20 pack (6)", href="/beer/all"), "no product link"),
    (card("$20 pack (6)"), "no product name"),
    (card("Foo", "Bar 330mL"), "no online prices"),
    (card("Foo", "Bar 330mL", "$5.99 each (in-store)"), "no online prices"),
])
def test_unusable_cards_are_errors_not_guesses(c, why):
    with pytest.raises(ValueError, match=why):
        dom.parse_card(c, K)


def test_a_price_with_no_unit_count_is_rejected():
    with pytest.raises(ValueError, match="without a unit count"):
        parse("Foo", "Bar 330mL", "$20 pack")


def test_a_multibuy_that_cannot_be_sized_is_rejected():
    with pytest.raises(ValueError, match="cannot size"):
        parse("Foo", "Bar 330mL", "MEMBER OFFER", "$42", "for 2 packs")


def test_duplicate_cards_keep_the_first():
    two = {"cards": [card("Foo", "Bar 330mL", "$20 pack (6)"), card("Foo", "Bar 330mL", "$99 pack (6)")]}
    products, errors = dom.parse_cards_payload(two, K)
    assert len(products) == 1 and products[0].prices[0].price == 20 and errors == []
    assert dom.count_unique(two["cards"]) == 1


def test_bad_payloads_are_rejected_not_half_parsed():
    for bad in ({}, {"cards": "x"}, {"cards": [{}] * (dom.MAX_CARDS + 1)}):
        with pytest.raises(ValueError):
            dom.parse_cards_payload(bad, K)
    products, errors = dom.parse_cards_payload({"cards": ["x", {"href": "/product/1/a", "lines": ["x"] * 99}]}, K)
    assert products == [] and len(errors) == 2


# ---- ingest ------------------------------------------------------------------------

STORE = {"store_id": "1546", "store_name": "Thornleigh", "suburb": "Thornleigh", "state": "NSW", "postcode": "2120"}


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("BEEROO_ADMIN_TOKEN", "t" * 32)
    return TestClient(create_app(str(tmp_path / "t.sqlite3")))


def test_cards_are_ingested_like_any_other_page(client):
    r = client.post("/api/admin/ingest", headers={"X-Admin-Token": "t" * 32},
                    json={"kind": "dan_murphys_cards", "location": STORE, "payload": CARDS})
    assert r.status_code == 200 and r.json()["products"] == 48 and r.json()["new_observations"] > 48


def test_cards_ingest_needs_a_location_and_a_matching_state(client):
    h = {"X-Admin-Token": "t" * 32}
    assert client.post("/api/admin/ingest", headers=h, json={"kind": "dan_murphys_cards", "payload": CARDS}).status_code == 422
    wrong = {**STORE, "state": "VIC"}
    assert client.post("/api/admin/ingest", headers=h,
                       json={"kind": "dan_murphys_cards", "location": wrong, "payload": CARDS}).status_code == 422


def test_garbage_cards_are_rejected_as_a_format_change(client):
    junk = {"cards": [card("Foo", "Bar", "no prices here")] * 10}
    r = client.post("/api/admin/ingest", headers={"X-Admin-Token": "t" * 32},
                    json={"kind": "dan_murphys_cards", "location": STORE, "payload": junk})
    assert r.status_code == 422


# ---- the browser driver picks cards when the page's JSON isn't seen -------------------


class FakePage:
    def __init__(self, cards):
        self.cards, self.handlers = cards, []

    def on(self, event, handler):
        self.handlers.append(handler)

    async def evaluate(self, js):
        assert "shop-product-card" in js
        return self.cards

    async def screenshot(self, **kw):
        pass


LOCATION = Location(retailer=Retailer.DAN_MURPHYS, store_id="1546", store_name="Thornleigh",
                    suburb="Thornleigh", state="NSW", postcode="2120")


@pytest.fixture()
def stubbed(monkeypatch):
    calls = []

    async def noop(*a, **k):
        calls.append("open")

    async def select(*a, **k):
        return LOCATION

    async def more_cards(*a, **k):
        calls.append("cards")

    async def more_pages(*a, **k):
        calls.append("pages")

    monkeypatch.setattr(dan_murphys, "open_category", noop)
    monkeypatch.setattr(dan_murphys, "select_location", select)
    monkeypatch.setattr(dan_murphys, "load_all_cards", more_cards)
    monkeypatch.setattr(dan_murphys, "load_all_pages", more_pages)
    monkeypatch.delenv("BEEROO_DM_METHOD", raising=False)
    return calls


def test_no_json_means_the_cards_are_read_as_text(stubbed):
    raw = []
    location, products, errors, total, extra = run(
        dan_murphys.scrape(FakePage(CARDS["cards"]), "2120", raw_pages=raw))
    assert (len(products), errors, total, extra) == (48, [], 48, {"kind": "dan_murphys_cards"})
    assert raw == [{"cards": CARDS["cards"]}] and "cards" in stubbed and "pages" not in stubbed
    assert location.location_key == LOCATION.location_key


def test_cards_scrape_result_ingests_and_counts_as_complete(stubbed, tmp_path):
    from app import ingest
    from common import db
    raw = []
    _, products, errors, total, extra = run(dan_murphys.scrape(FakePage(CARDS["cards"]), "2120", raw_pages=raw))
    body = ingest.IngestIn.model_validate({"kind": extra["kind"], "location": STORE, "payload": raw[0]})
    result = ingest.ingest_trusted(db.connect(str(tmp_path / "x.sqlite3")), body)
    assert result["products"] == len(products) == total


def test_a_seen_json_page_wins_in_auto_mode(stubbed, monkeypatch):
    browse = json.loads((F / "dan_murphys_browse_page1.json").read_text())
    original = dan_murphys.BrowseCollector

    class Seen(original):
        def __init__(self):
            super().__init__()
            self.pages = {1: browse}
            self.total = 24

    monkeypatch.setattr(dan_murphys, "BrowseCollector", Seen)
    raw = []
    result = run(dan_murphys.scrape(FakePage(CARDS["cards"]), "2120", raw_pages=raw))
    assert len(result) == 4 and len(result[1]) == 24            # JSON path: no "kind" override
    assert raw[0].pop("ratings")                                # plus what the page's stars/counts say
    assert raw == [browse] and "pages" in stubbed and "cards" not in stubbed


def test_cards_mode_ignores_json_and_json_mode_needs_it(stubbed, monkeypatch):
    monkeypatch.setenv("BEEROO_DM_METHOD", "cards")
    assert run(dan_murphys.scrape(FakePage(CARDS["cards"]), "2120", raw_pages=[]))[4] == {"kind": "dan_murphys_cards"}
    monkeypatch.setenv("BEEROO_DM_METHOD", "nonsense")
    with pytest.raises(ValueError):
        run(dan_murphys.scrape(FakePage(CARDS["cards"]), "2120", raw_pages=[]))


# ---- layout quirks seen on the live page (2026-10 import) ------------------------------


def test_odd_spacing_and_typos_in_the_unit_count_are_tolerated():
    p = parse("Stone & Wood", "Stone Beer Cans 375mL", "$75.99 case(16)", "$24.99 pack((4)", "ADD TO CART ")
    assert options(p) == [("case", 16, 75.99, False), ("pack", 4, 24.99, False)]
    q = parse("Wrexham", "Lager 4.0 Bottles 330mL", "$21.99 pack (6)", "$64.99 case( 24)", "DELIVERY ONLY")
    assert options(q) == [("case", 24, 64.99, False), ("pack", 6, 21.99, False)]


def test_one_unsizable_option_does_not_cost_the_card_its_other_prices():
    p = parse("Foo", "Bar 330mL", "$20 pack (6)", "$99 per kit")
    assert options(p) == [("pack", 6, 20.0, False)]
    with pytest.raises(ValueError, match="without a unit count"):
        parse("Coopers", "DIY Home Brewing Kit", "$149 per kit")


def test_a_few_odd_cards_do_not_sink_a_whole_page(client):
    odd = [card("Coopers", "DIY Home Brewing Kit", "$149 per kit", href=f"/product/DM_{i}/kit") for i in range(5)]
    page = {"cards": CARDS["cards"] + odd + [{"href": "", "lines": [""]}]}
    r = client.post("/api/admin/ingest", headers={"X-Admin-Token": "t" * 32},
                    json={"kind": "dan_murphys_cards", "location": STORE, "payload": page})
    assert r.status_code == 200 and r.json()["products"] == 48 and r.json()["skipped"] == 6


def test_but_a_page_where_most_cards_fail_is_still_rejected_as_a_format_change(client):
    broken = [card("Foo", "Bar", "$20 per kit", href=f"/product/DM_{i}/x") for i in range(40)]
    r = client.post("/api/admin/ingest", headers={"X-Admin-Token": "t" * 32},
                    json={"kind": "dan_murphys_cards", "location": STORE, "payload": {"cards": CARDS["cards"][:10] + broken}})
    assert r.status_code == 422


# ---- "Load more": the button appears a moment after the data (a cloud run stopped at 24 of 392) ----------------

from playwright.async_api import TimeoutError as PWTimeout  # noqa: E402


class LateButton:
    """The site's "Load more": takes `appears_after` ms to be drawn after the data arrives (Playwright's wait_for
    polls up to its timeout); clicking it adds a page of products. It's gone once everything is loaded."""

    def __init__(self, page, appears_after=0):
        self.page, self.first, self.waits, self.appears_after = page, self, 0, appears_after

    async def wait_for(self, state="visible", timeout=0):
        self.waits += 1
        if self.page.done() or self.appears_after > timeout:
            raise PWTimeout("not drawn in time")

    async def scroll_into_view_if_needed(self):
        pass

    async def click(self, timeout=0):
        if self.page.fail_clicks:
            self.page.fail_clicks -= 1
            raise PWTimeout("covered")
        self.page.collector.add_page()


class SiteCollector:
    def __init__(self, total, per_page=24):
        self.pages, self.total, self.per_page = {}, total, per_page
        self.add_page()

    def add_page(self):
        n = len(self.pages) + 1
        self.pages[n] = {"Bundles": [{"Products": [{}] * min(self.per_page, self.total - len(self.pages) * self.per_page)}]}

    async def wait_for_new_page(self, timeout_s=20):
        pass


class SitePage:
    def __init__(self, total, appears_after=0, fail_clicks=0):
        self.collector, self.fail_clicks = SiteCollector(total), fail_clicks
        self.button = LateButton(self, appears_after)

    def done(self):
        return sum(len(b["Products"]) for p in self.collector.pages.values() for b in p["Bundles"]) >= self.collector.total

    def locator(self, selector):
        assert selector == dan_murphys.LOAD_MORE
        return self.button


def loaded(page):
    return sum(len(b["Products"]) for p in page.collector.pages.values() for b in p["Bundles"])


@pytest.fixture()
def fast(monkeypatch):
    monkeypatch.setattr(dan_murphys, "PAUSE_RANGE_S", (0, 0))


def test_a_button_that_is_drawn_late_is_waited_for_not_given_up_on(fast):
    page = SitePage(total=96, appears_after=4000)          # drawn 4 s after the data: a check at once would miss it
    run(dan_murphys.load_all_pages(page, page.collector))
    assert loaded(page) == 96 and len(page.collector.pages) == 4


def test_a_button_that_never_comes_ends_the_list_and_says_so(fast, caplog):
    page = SitePage(total=96, appears_after=60_000)
    with caplog.at_level("WARNING", logger="beeroo.scrape"):
        run(dan_murphys.load_all_pages(page, page.collector))
    assert loaded(page) == 24
    assert "no 'Load more' button" in caplog.text and "24 of 96" in caplog.text


def test_a_click_that_is_blocked_once_is_retried(fast):
    page = SitePage(total=48, fail_clicks=1)
    run(dan_murphys.load_all_pages(page, page.collector))
    assert loaded(page) == 48


def test_a_button_that_cannot_be_clicked_gives_up_with_a_message(fast, caplog):
    page = SitePage(total=48, fail_clicks=5)
    with caplog.at_level("WARNING", logger="beeroo.scrape"):
        run(dan_murphys.load_all_pages(page, page.collector))
    assert loaded(page) == 24 and "could not click" in caplog.text


def test_nothing_is_clicked_when_everything_is_already_loaded(fast):
    page = SitePage(total=20)
    run(dan_murphys.load_all_pages(page, page.collector))
    assert page.button.waits == 0 and loaded(page) == 20


# ---- ratings: the Browse JSON says 0 reviews for everything; the page shows the real ones ------------------------------


def rated_cards():
    cards = json.loads(json.dumps(CARDS["cards"]))
    for c, rating in zip(cards, (4.54, 3.0, 5.0)):
        c["rating"] = rating
    return cards


def test_ratings_come_from_the_cards_by_sku():
    ratings = dom.ratings_from_cards(rated_cards())
    assert ratings["587292"] == [4.54, 116]
    assert len(ratings) > 20 and all(len(v) == 2 for v in ratings.values())
    assert dom.ratings_from_cards([{"href": "/beer/all", "lines": ["(5 REVIEWS)"]}, "junk", None]) == {}


def test_the_pages_ratings_replace_the_jsons_zeros():
    browse = json.loads((F / "dan_murphys_browse_page1.json").read_text())
    sku = str(browse["Bundles"][0]["Products"][0]["Stockcode"])
    page = {**browse, "ratings": {sku: [4.2, 37], "999": [1, 1]}}
    products, errors = parse_browse(page)
    first = next(p for p in products if p.listing.retailer_sku == sku)
    assert (first.listing.rating, first.listing.review_count) == (4.2, 37)
    assert all(p.listing.rating is None for p in products if p is not first)


def test_json_ratings_are_kept_if_the_page_has_none_and_junk_is_ignored():
    browse = json.loads((F / "dan_murphys_browse_page1.json").read_text())
    product = browse["Bundles"][0]["Products"][0]
    product["OverallRating"], product["NumberOfReviews"] = 4.4, 12
    for junk in ({"ratings": "x"}, {"ratings": {str(product["Stockcode"]): "bad"}}, {"ratings": {str(product["Stockcode"]): [0, 0]}}):
        products, _ = parse_browse({**browse, **junk})
        assert next(p for p in products if p.listing.retailer_sku == str(product["Stockcode"])).listing.rating == 4.4


def parse_browse(page):
    from scrapers import endeavour
    return endeavour.parse_browse_payload(page, K)


def test_the_json_path_reads_stars_from_the_page_and_sends_them_with_the_pages(stubbed, monkeypatch):
    browse = json.loads((F / "dan_murphys_browse_page1.json").read_text())
    sku = str(browse["Bundles"][0]["Products"][0]["Stockcode"])
    cards = [{"href": f"/product/DM_{sku}/x", "rating": 4.1, "lines": ["(52 REVIEWS)", "Foo", "Bar 330mL", "$20 pack (6)"]}]
    original = dan_murphys.BrowseCollector

    class Seen(original):
        def __init__(self):
            super().__init__()
            self.pages, self.total = {1: browse}, 24

    monkeypatch.setattr(dan_murphys, "BrowseCollector", Seen)
    raw = []
    _, products, _, _ = run(dan_murphys.scrape(FakePage(cards), "2120", raw_pages=raw))
    first = next(p for p in products if p.listing.retailer_sku == sku)
    assert (first.listing.rating, first.listing.review_count) == (4.1, 52)
    assert raw[0]["ratings"] == {sku: [4.1, 52]}


def test_a_failure_reading_the_stars_never_costs_the_prices(stubbed, monkeypatch, caplog):
    browse = json.loads((F / "dan_murphys_browse_page1.json").read_text())
    original = dan_murphys.BrowseCollector

    class Seen(original):
        def __init__(self):
            super().__init__()
            self.pages, self.total = {1: browse}, 24

    class Broken(FakePage):
        async def evaluate(self, js):
            raise RuntimeError("page closed")

    monkeypatch.setattr(dan_murphys, "BrowseCollector", Seen)
    raw = []
    with caplog.at_level("WARNING", logger="beeroo.scrape"):
        _, products, _, _ = run(dan_murphys.scrape(Broken([]), "2120", raw_pages=raw))
    assert len(products) == 24 and "ratings" not in raw[0] and "star ratings" in caplog.text
