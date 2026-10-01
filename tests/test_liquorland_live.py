import asyncio
import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from scrapers import liquorland, liquorland_live
from scrapers.dan_murphys import Blocked, raise_if_blocked

F = Path(__file__).parent / "fixtures"
NOW = datetime(2026, 10, 1, tzinfo=timezone.utc)


def run(coro):
    return asyncio.run(coro)


class FakeResponse:
    def __init__(self, url, payload, method="GET"):
        self.url, self._payload = url, payload
        self.request = type("R", (), {"method": method})()

    async def json(self):
        if isinstance(self._payload, Exception):
            raise self._payload
        return self._payload


def page_payload(site, ids, current=1, total=987):
    return {"debugQuery": f"state=nsw&sitestate={site}&page_type=default",
            "meta": {"page": {"current": current, "total": 13, "size": 80, "productCount": total}},
            "products": [{"id": i, "name": f"Beer {i} 330mL", "isAvailable": True, "unitOfMeasure": "ea",
                          "price": {"current": 5.0}, "volumeMl": 330, "productUrl": f"/beer-and-cider/b_{i}"} for i in ids]}


# ---- list pages -------------------------------------------------------------


@pytest.mark.parametrize("query,expected", [
    ("page=3&sort=&show=60&v=2&facets=beer", 3),
    ("fh_start_index=160&show=80", 3),
    ("fh_start_index=120&fh_view_size=60", 3),
    ("", 7),
    ("page=abc", 7),
])
def test_page_number_comes_from_the_request_not_meta(query, expected):
    payload = {"meta": {"page": {"current": 7}}}
    assert liquorland_live.page_number(query, payload, 99) == expected or (query in ("", "page=abc") and liquorland_live.page_number(query, payload, 99) == 7)


def test_collector_keeps_pages_per_state_and_ignores_everything_else():
    c = liquorland_live.LiquorlandCollector()
    base = "https://www.liquorland.com.au/api/products/ll"
    run(c.on_response(FakeResponse(f"{base}/act/beer-and-cider?page=1", page_payload("ll_act", ["a1"]))))
    run(c.on_response(FakeResponse(f"{base}/nsw/beer-and-cider?page=1", page_payload("ll_nsw", ["n1"]))))
    run(c.on_response(FakeResponse(f"{base}/nsw/beer-and-cider?page=2", page_payload("ll_nsw", ["n2"], 2))))
    run(c.on_response(FakeResponse(f"{base}/nsw/beer-and-cider/3813708_ea", {"product": {}})))          # detail: ignored
    run(c.on_response(FakeResponse(f"{base}/nsw/wine", page_payload("ll_nsw", ["w"]))))                  # other category
    run(c.on_response(FakeResponse(f"{base}/nsw/beer-and-cider?page=3", page_payload("ll_nsw", ["x"]), "POST")))
    run(c.on_response(FakeResponse(f"{base}/nsw/beer-and-cider?page=4", ValueError("not json"))))
    assert sorted(c.pages) == [("ll_act", 1), ("ll_nsw", 1), ("ll_nsw", 2)]
    assert [p["products"][0]["id"] for p in c.for_site("ll_nsw")] == ["n1", "n2"]      # ordered by page
    assert c.for_site("ll_wa") == [] and c.total == 987
    c.reset()
    assert c.pages == {} and c.total is None


def test_collector_remembers_the_nearby_stores_lookup():
    c = liquorland_live.LiquorlandCollector()
    run(c.on_response(FakeResponse("https://www.liquorland.com.au/api/inventory/ll/findnearby/%7Bx%7D?lat=1", {"stores": [{"store": {"storeName": "A"}}]})))
    assert c.nearby["stores"][0]["store"]["storeName"] == "A"


def test_only_the_beer_and_cider_list_path_matches():
    ok = liquorland_live._LIST_PATH
    assert ok.match("/api/products/ll/nsw/beer-and-cider") and ok.match("/api/products/ll/wa/beer-and-cider/")
    for bad in ("/api/products/ll/nsw/beer-and-cider/3813708_ea", "/api/products/ll_act/beer-and-cider",
                "/api/products/ll/nsw/wine", "/api/auth/ll/anonymous-access-token"):
        assert not ok.match(bad), bad


# ---- minimal payload --------------------------------------------------------


def test_minimal_payload_parses_identically_and_is_much_smaller():
    raw = json.loads((F / "liquorland_act_products.json").read_text())
    slim = liquorland.minimal_payload(raw)
    a, ea = liquorland.parse_liquorland_payload(raw, observed_at=NOW)
    b, eb = liquorland.parse_liquorland_payload(slim, observed_at=NOW)
    snap = lambda ps: sorted((p.listing.retailer_sku, p.listing.name, p.listing.unit_volume_ml,
                              tuple(sorted((o.pack_type.value, o.units, o.member_only, o.price) for o in p.prices))) for p in ps)
    assert snap(a) == snap(b) and len(a) == 40 and ea == eb
    assert len(json.dumps(slim)) < len(json.dumps(raw)) * 0.7


def test_minimal_payload_keeps_only_the_site_state_and_needed_fields():
    raw = page_payload("ll_nsw", ["1", "2"])
    raw["products"][0]["image"] = {"heroImage": "x"}
    raw["products"][0]["ratings"] = {"average": 5}
    raw["debugQuery"] += "&date_time=20261001T135000&fh_location=//catalog01/en_AU/x"
    slim = liquorland.minimal_payload(raw)
    assert slim["debugQuery"] == "sitestate=ll_nsw"
    text = json.dumps(slim)
    assert "heroImage" not in text and "ratings" not in text and "date_time" not in text
    assert slim["meta"]["page"]["productCount"] == 987


# ---- bot protection ---------------------------------------------------------


class FakePage:
    def __init__(self, title="Buy Beer | Liquorland", url="https://www.liquorland.com.au/beer-and-cider/beer"):
        self._title, self.url = title, url

    async def title(self):
        return self._title


class FakeStatus:
    def __init__(self, status):
        self.status = status


@pytest.mark.parametrize("title,url,status", [
    ("Attention Required! | Cloudflare", "https://www.danmurphys.com.au/beer/all", 403),
    ("ShieldSquare Captcha", "https://validate.perfdrive.com/x", 200),
    ("Please solve this CAPTCHA", "https://www.liquorland.com.au/x", 200),
    ("Buy Beer", "https://validate.perfdrive.com/abc", 200),
    ("Buy Beer", "https://www.liquorland.com.au/x", 403),
    ("Buy Beer", "https://www.liquorland.com.au/x", 429),
])
def test_bot_protection_and_captcha_pages_stop_the_run(title, url, status):
    with pytest.raises(Blocked):
        run(raise_if_blocked(FakePage(title, url), FakeStatus(status)))


def test_normal_pages_are_not_mistaken_for_blocks():
    run(raise_if_blocked(FakePage(), FakeStatus(200)))
    run(raise_if_blocked(FakePage("Buy Beer Online | BWS", "https://bws.com.au/beer"), None))


def test_the_driver_contains_no_captcha_solving_or_stealth_code():
    """A tripwire for the project rule: stop on bot protection, never evade it."""
    import re
    for module in ("liquorland_live", "bws_live", "dan_murphys"):
        text = (Path(__file__).parent.parent / "scrapers" / f"{module}.py").read_text().lower()
        for bad in ("stealth", "undetected", "2captcha", "anticaptcha", "solve_captcha", "proxy", "user_agent=", "webdriver"):
            assert bad not in re.sub(r"never (try to )?solve[^\n]*", "", text), (module, bad)
