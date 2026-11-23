import asyncio
import json
from pathlib import Path

import pytest

from scrapers import liquorland_apify as la

FIX = Path(__file__).parent / "fixtures"


def raw(current=1, total=1):
    d = json.loads((FIX / "liquorland_act_products.json").read_text())
    d["meta"]["page"].update(current=current, total=total)
    return d


def test_input_reads_the_beer_list_for_the_state():
    i = la.actor_input("ACT", 2)
    assert i["operation"] == "category" and i["path"] == "/beer-and-cider" and i["facets"] == "beer"
    assert i["state"] == "act" and i["page"] == 2 and i["includeRaw"] is True


def test_raw_list_is_found_anywhere_in_output():
    page = la.to_page({"status": "ok", "raw": {"response": raw()}}, [], "ACT")
    assert page["debugQuery"] == "sitestate=ll_act" and len(page["products"]) == 40
    assert page["meta"]["page"]["productCount"] == 40


def test_items_with_site_fields_are_used_when_no_raw():
    items = raw()["products"]
    page = la.to_page({"pagination": {"current": 1, "total": 3, "size": 60, "productCount": 150}}, items, "ACT")
    assert page["debugQuery"] == "sitestate=ll_act" and page["meta"]["page"]["total"] == 3


def test_wrong_state_is_refused():
    with pytest.raises(la.ApifyError, match="ll_wa"):
        la.to_page({"raw": raw()}, [], "WA")


def test_unrecognised_result_keeps_a_sample(tmp_path, monkeypatch):
    monkeypatch.setattr(la, "DEBUG_DIR", tmp_path)
    with pytest.raises(la.ApifyError, match="no recognisable"):
        la.to_page({"total": 1}, [{"name": "x"}], "NSW")
    assert (tmp_path / "liquorland-apify-nsw.json").exists()


def test_run_actor_polls_then_reads_output_and_items():
    calls = []

    def request(method, url, token, body=None, timeout=120):
        calls.append((method, url.split("?")[0]))
        assert token == "t" and "token=" not in url
        if method == "POST":
            return {"data": {"id": "r1", "status": "RUNNING"}}
        if "/actor-runs/" in url:
            return {"data": {"id": "r1", "status": "SUCCEEDED", "defaultKeyValueStoreId": "k", "defaultDatasetId": "d"}}
        if "/records/OUTPUT" in url:
            return {"raw": raw()}
        return [{"a": 1}]

    output, items = la.run_actor("t", {}, request=request, sleep=lambda s: None)
    assert output["raw"]["debugQuery"] == "sitestate=ll_act" and items == [{"a": 1}]
    assert [c[0] for c in calls] == ["POST", "GET", "GET", "GET"]


def test_failed_run_raises_with_its_message():
    def request(method, url, token, body=None, timeout=120):
        if method == "POST":
            return {"data": {"id": "r1", "status": "FAILED", "statusMessage": "blocked", "defaultKeyValueStoreId": "k"}}
        return {"error": "access not restored"}

    with pytest.raises(la.ApifyError, match="FAILED: blocked"):
        la.run_actor("t", {}, request=request, sleep=lambda s: None)


def test_scrape_fetches_every_page_and_matches_the_live_shape():
    asked = []

    def run(token, run_input):
        asked.append(run_input["page"])
        page = raw(current=run_input["page"], total=3)
        for p in page["products"]:                       # distinct entries per page
            p["id"] = f"{p['id']}-{run_input['page']}"
        return {"raw": page}, []

    result = asyncio.run(la.make_scrape("t", run=run)("2600", 30))
    assert sorted(asked) == [1, 2, 3]
    assert result["location"].location_key == "liquorland:ll_act"
    assert len(result["raw_pages"]) == 3 and result["collected"] == 120
    assert all(p["debugQuery"] == "sitestate=ll_act" for p in result["raw_pages"])


def test_real_scrapes_uses_apify_only_with_a_token():
    import sys
    sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))
    import scheduled_scrape as ss

    with_token = ss.real_scrapes(environ={"APIFY_TOKEN": "t"})["liquorland"]
    without = ss.real_scrapes(environ={})["liquorland"]
    assert with_token.__module__ == "scrapers.liquorland_apify"
    assert without.__module__ != "scrapers.liquorland_apify"
