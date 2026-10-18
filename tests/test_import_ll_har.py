import base64
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))
import import_ll_har as imp  # noqa: E402

from common import db  # noqa: E402

FIX = Path(__file__).parent / "fixtures"


@pytest.fixture(autouse=True)
def no_real_env_file(monkeypatch):
    """Never read the developer's real ~/.config/beeroo/env (it may point at the live cloud inbox)."""
    monkeypatch.setattr(imp, "load_env_file", lambda **kw: None)


def payload(name, **page):
    d = json.loads((FIX / f"liquorland_{name}_products.json").read_text())
    d["meta"]["page"].update(page)
    return d


def entry(url, body, status=200, started="2026-10-17T09:30:00.000Z", b64=False):
    text = json.dumps(body)
    content = {"mimeType": "application/json", "text": base64.b64encode(text.encode()).decode() if b64 else text}
    if b64:
        content["encoding"] = "base64"
    return {"startedDateTime": started, "request": {"method": "GET", "url": url}, "response": {"status": status, "content": content}}


def har(tmp_path, *entries, name="x.har"):
    p = tmp_path / name
    p.write_text(json.dumps({"log": {"version": "1.2", "entries": list(entries)}}))
    return str(p)


LIST = "https://www.liquorland.com.au/api/products/ll/{}/beer-and-cider?page={}&show=80"


def test_reads_list_responses_and_ignores_everything_else(tmp_path):
    f = har(tmp_path,
            entry("https://www.liquorland.com.au/static/js/main.js", {"products": []}),
            entry(LIST.format("act", 1), payload("act")),
            entry(LIST.format("act", 2), payload("act"), status=500),
            entry("https://www.liquorland.com.au/api/products/ll/wa/beer-and-cider?page=1", payload("wa"), b64=True))
    pages = imp.read_pages(f)
    assert len(pages) == 2
    states = imp.group_by_state(pages)
    assert set(states) == {"ll_act", "ll_wa"}                       # the state comes from the page, not from the user


def test_a_page_seen_twice_counts_once_and_the_latest_wins(tmp_path):
    older, newer = payload("act"), payload("act")
    older["products"][0]["price"]["current"] = 1.0
    f = har(tmp_path, entry(LIST.format("act", 1), older, started="2026-10-17T09:00:00Z"),
            entry(LIST.format("act", 1), newer, started="2026-10-17T09:05:00Z"))
    states = imp.group_by_state(imp.read_pages(f))
    assert len(states["ll_act"]["pages"]) == 1 and states["ll_act"]["pages"][0]["products"][0]["price"]["current"] != 1.0
    assert states["ll_act"]["observed_at"].isoformat().startswith("2026-10-17T09:05")


def test_dry_run_reports_each_state_and_sends_nothing(tmp_path, capsys, monkeypatch):
    monkeypatch.setattr(imp, "send", lambda *a, **k: pytest.fail("dry run must not send"))
    f = har(tmp_path, entry(LIST.format("act", 1), payload("act")), entry(LIST.format("wa", 1), payload("wa")))
    assert imp.main([f, "--dry-run"], environ={}) == 0
    out = capsys.readouterr().out
    assert "ACT: 40 of 40 products" in out and "WA: 23 of 23 products" in out and "dry run" in out


def test_a_partly_paged_list_is_refused_unless_forced(tmp_path, capsys):
    f = har(tmp_path, entry(LIST.format("act", 1), payload("act", productCount=400)))
    assert imp.main([f, "--dry-run"], environ={}) == 1
    err = capsys.readouterr().err
    assert "ACT: only 40 of 400" in err and "Keep paging" in err
    assert imp.main([f, "--dry-run", "--force"], environ={}) == 0


def test_a_har_without_the_beer_list_says_how_to_fix_it(tmp_path, capsys):
    f = har(tmp_path, entry("https://www.liquorland.com.au/", {"hello": 1}))
    assert imp.main([f], environ={}) == 1
    assert "Save all as HAR with content" in capsys.readouterr().err


def test_import_into_the_local_database_files_prices_under_the_pages_own_state(tmp_path, capsys):
    f = har(tmp_path, entry(LIST.format("act", 1), payload("act")), entry(LIST.format("wa", 1), payload("wa")))
    database = tmp_path / "beeroo.sqlite3"
    assert imp.main([f, "--local", "--db", str(database)], environ={}) == 0
    conn = db.connect(str(database))
    keys = {r[0] for r in conn.execute("SELECT DISTINCT location_key FROM price_observations")}
    assert keys == {"liquorland:ll_act", "liquorland:ll_wa"}
    assert "saved to" in capsys.readouterr().out
    # importing the same file again adds no new prices
    imp.main([f, "--local", "--db", str(database)], environ={})
    assert "0 new price observations" in capsys.readouterr().out


def test_with_a_cloud_remote_pages_go_to_the_liquorland_inbox(tmp_path, monkeypatch, capsys):
    import data_branch
    pushed = {}

    def fake_push(remote, source, retailer):
        pushed.update(remote=remote, retailer=retailer, files=sorted(p.name for p in Path(source).glob("*.json")))
        for p in Path(source).glob("*.json"):
            p.unlink()
        return len(pushed["files"])

    monkeypatch.setattr(data_branch, "push_inbox", fake_push)
    monkeypatch.setattr(imp, "OUTBOX", tmp_path / "outbox")
    f = har(tmp_path, entry(LIST.format("act", 1), payload("act")))
    imp.main([f], environ={"BEEROO_DATA_REMOTE": "https://example/x.git"})
    assert pushed["retailer"] == "liquorland" and len(pushed["files"]) == 1
    assert "cloud inbox" in capsys.readouterr().out
