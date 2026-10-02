import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))
import data_branch  # noqa: E402
import import_dm_page as imp  # noqa: E402

from common import db  # noqa: E402
from scrapers import dan_murphys_dom as dom  # noqa: E402

F = Path(__file__).parent / "fixtures"
HTML = F / "dan_murphys_page_sample.html"            # 7 real cards from a saved page
CARDS = json.loads((F / "dan_murphys_cards.json").read_text())   # 48 real cards
WHEN = datetime(2026, 10, 2, 3, 0, tzinfo=timezone.utc)


def body(cards=None, state="NSW", **kw):
    return imp.build_body(cards if cards is not None else CARDS["cards"], state, WHEN, **kw)


# ---- reading the saved page ----------------------------------------------------------


def test_a_saved_html_page_yields_the_cards_with_links_and_text():
    cards = imp.read_cards(HTML)
    assert len(cards) == 7
    assert all("/product/" in c["href"] for c in cards)
    first = " ".join(cards[0]["lines"])
    assert "San Miguel" in first and "$71.99" in first and "case (24)" in first


def test_scripts_inside_cards_and_text_outside_them_are_ignored():
    html = ("<body>Header text<shop-product-card><script>var junk=1</script>"
            "<a href='/product/DM_9/x'>Foo</a><span>Bar 330mL</span><b>$20 pack (6)</b></shop-product-card>"
            "<p>footer</p></body>")
    assert imp.cards_from_html(html) == [{"href": "/product/DM_9/x", "lines": ["Foo", "Bar 330mL", "$20 pack (6)"]}]


def test_html_cards_parse_the_same_as_the_console_json():
    from_html = dom.parse_cards_payload({"cards": imp.read_cards(HTML)}, "k")[0]
    assert len(from_html) == 7
    by_sku = {p.listing.retailer_sku: p for p in dom.parse_cards_payload(CARDS, "k")[0]}
    for p in from_html:
        twin = by_sku[p.listing.retailer_sku]
        assert p.listing.name == twin.listing.name
        assert sorted((o.units, o.price, o.member_only) for o in p.prices) == sorted((o.units, o.price, o.member_only) for o in twin.prices)


def test_json_input_either_shape(tmp_path):
    (tmp_path / "a.json").write_text(json.dumps(CARDS))
    (tmp_path / "b.json").write_text(json.dumps(CARDS["cards"]))
    assert len(imp.read_cards(tmp_path / "a.json")) == len(imp.read_cards(tmp_path / "b.json")) == 48
    (tmp_path / "c.json").write_text('{"nope": 1}')
    (tmp_path / "d.json").write_text("{broken")
    for bad in ("c.json", "d.json", "missing.json"):
        with pytest.raises(imp.ImportProblem):
            imp.read_cards(tmp_path / bad)


# ---- building and checking -------------------------------------------------------------


def test_location_is_filed_under_the_chosen_state_with_its_pricing_postcode():
    b = body(state="vic")
    assert (b["location"]["state"], b["location"]["postcode"], b["location"]["store_id"]) == ("VIC", "3000", "manual_vic")
    assert body(store={"store_id": "1546", "store_name": "Thornleigh"})["location"]["store_id"] == "1546"
    with pytest.raises(imp.ImportProblem):
        body(state="Narnia")


def test_a_good_page_checks_out():
    products, errors, unique = imp.check(body())
    assert (len(products), errors, unique) == (48, [], 48)


def test_a_half_loaded_page_is_refused_unless_forced():
    few = CARDS["cards"][:10]
    with pytest.raises(imp.ImportProblem, match="Load more"):
        imp.check(body(few))
    assert imp.check(body(few), force=True)[2] == 10


def test_nothing_useful_is_refused():
    with pytest.raises(imp.ImportProblem, match="no product cards"):
        imp.check(body([]))
    junk = [{"href": f"/product/{i}/x", "lines": ["Foo", "no price"]} for i in range(60)]
    with pytest.raises(imp.ImportProblem, match="too many cards|no products"):
        imp.check(body(junk))


# ---- sending -----------------------------------------------------------------------------


def test_local_import_writes_the_database(tmp_path):
    msg = imp.send(body(), local=True, db_path=tmp_path / "x.sqlite3", outbox=tmp_path / "out", environ={})
    conn = db.connect(str(tmp_path / "x.sqlite3"))
    assert "48 products" in msg and conn.execute("SELECT COUNT(*) FROM listings").fetchone()[0] == 48
    loc = conn.execute("SELECT * FROM locations").fetchone()
    assert (loc["retailer"], loc["state"]) == ("dan_murphys", "NSW")


def test_without_a_cloud_remote_it_imports_locally(tmp_path):
    msg = imp.send(body(), db_path=tmp_path / "x.sqlite3", outbox=tmp_path / "out", environ={})
    assert msg.startswith("saved to")


def test_with_a_cloud_remote_the_page_goes_to_the_inbox(tmp_path):
    bare = tmp_path / "origin.git"
    subprocess.run(["git", "init", "--bare", "-q", "-b", "main", str(bare)], check=True)
    msg = imp.send(body(), outbox=tmp_path / "out", environ={"BEEROO_DATA_REMOTE": str(bare)})
    assert "cloud inbox" in msg and list((tmp_path / "out").glob("*.json")) == []
    data_branch.restore(str(bare), tmp_path / "c", tmp_path / "d")
    [page] = list((tmp_path / "c" / "inbox" / "dan_murphys").glob("*.json"))
    sent = json.loads(page.read_text())
    assert sent["kind"] == "dan_murphys_cards" and sent["location"]["state"] == "NSW"


def test_an_unreachable_cloud_keeps_the_page_for_the_nightly_retry(tmp_path):
    msg = imp.send(body(), outbox=tmp_path / "out",
                   environ={"BEEROO_DATA_REMOTE": str(tmp_path / "nowhere.git")})
    assert "nightly run will retry" in msg and len(list((tmp_path / "out").glob("*.json"))) == 1


# ---- the env file ---------------------------------------------------------------------------


def test_env_file_is_read_only_if_private(tmp_path):
    env = tmp_path / "env"
    env.write_text("# comment\nBEEROO_DATA_REMOTE='https://example/x.git'\nexport OTHER=1\n")
    env.chmod(0o600)
    target = {"OTHER": "keep"}
    imp.load_env_file(env, target)
    assert target == {"OTHER": "keep", "BEEROO_DATA_REMOTE": "https://example/x.git"}
    env.chmod(0o644)
    with pytest.raises(imp.ImportProblem, match="chmod 600"):
        imp.load_env_file(env, {})
    imp.load_env_file(tmp_path / "missing", {})            # no file is fine


# ---- the command ------------------------------------------------------------------------------


@pytest.fixture()
def no_env(monkeypatch, tmp_path):
    monkeypatch.setattr(imp, "ENV_FILE", tmp_path / "no-env")
    monkeypatch.delenv("BEEROO_DATA_REMOTE", raising=False)
    monkeypatch.setattr(imp, "load_env_file", lambda *a, **k: None)


def test_main_dry_run_sends_nothing(no_env, tmp_path, capsys):
    path = tmp_path / "dm.json"
    path.write_text(json.dumps(CARDS))
    code = imp.main([str(path), "--state", "NSW", "--dry-run", "--db", str(tmp_path / "x.sqlite3")])
    assert code == 0 and "48 products found" in capsys.readouterr().out
    assert not (tmp_path / "x.sqlite3").exists()


def test_main_imports_and_is_safe_to_repeat(no_env, tmp_path, capsys):
    path = tmp_path / "dm.json"
    path.write_text(json.dumps(CARDS))
    args = [str(path), "--state", "NSW", "--db", str(tmp_path / "x.sqlite3")]
    assert imp.main(args) == 0 and imp.main(args) == 0
    conn = db.connect(str(tmp_path / "x.sqlite3"))
    assert conn.execute("SELECT COUNT(*) FROM listings").fetchone()[0] == 48
    out = capsys.readouterr().out
    assert "0 new price observations" in out.splitlines()[-1]          # the second run adds nothing


def test_main_reports_problems_with_a_nonzero_exit(no_env, tmp_path, capsys):
    path = tmp_path / "dm.json"
    path.write_text(json.dumps({"cards": CARDS["cards"][:5]}))
    assert imp.main([str(path), "--state", "NSW", "--db", str(tmp_path / "x.sqlite3")]) == 1
    assert "Load more" in capsys.readouterr().err
    assert imp.main([str(path), "--state", "XX"]) == 1
    assert imp.main([str(tmp_path / "missing.json"), "--state", "NSW"]) == 1


def test_imported_prices_show_up_for_that_state_only(no_env, tmp_path):
    from app.queries import resolve_locations
    imp.send(body(), local=True, db_path=tmp_path / "x.sqlite3", outbox=tmp_path / "o", environ={})
    conn = db.connect(str(tmp_path / "x.sqlite3"))
    assert resolve_locations(conn, "NSW")["retailers"]["dan_murphys"]["state"] == "NSW"
    assert resolve_locations(conn, "VIC")["retailers"]["dan_murphys"] is None
