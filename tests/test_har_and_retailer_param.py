import base64
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))

import har_to_fixtures  # noqa: E402

from common.records import Retailer  # noqa: E402
from scrapers.endeavour import parse_browse_payload  # noqa: E402

FIXTURE = Path(__file__).parent / "fixtures" / "dan_murphys_browse_page1.json"


def test_endeavour_parser_is_retailer_parameterised():
    payload = json.loads(FIXTURE.read_text())
    products, errors = parse_browse_payload(
        payload, "bws:1", retailer=Retailer.BWS, base_url="https://bws.com.au"
    )
    assert errors == []
    assert all(p.listing.retailer is Retailer.BWS for p in products)
    assert all(p.listing.url.startswith("https://bws.com.au/product/") for p in products)
    assert all(o.location_key == "bws:1" for p in products for o in p.prices)


def make_har(tmp_path):
    secret_headers = [{"name": "Cookie", "value": "SESSION=supersecret"}]
    har = {"log": {"entries": [
        {"request": {"method": "POST", "url": "https://api.example.com/apis/ui/Browse", "headers": secret_headers},
         "response": {"content": {"mimeType": "application/json", "text": '{"ok": 1}'}}},
        {"request": {"method": "GET", "url": "https://api.example.com/other", "headers": secret_headers},
         "response": {"content": {"mimeType": "application/json", "text": '{"skip": 1}'}}},
        {"request": {"method": "GET", "url": "https://www.example.com/beer", "headers": []},
         "response": {"content": {"mimeType": "text/html", "encoding": "base64",
                                  "text": base64.b64encode(b"<html>hi</html>").decode()}}},
        {"request": {"method": "GET", "url": "https://www.example.com/x.png", "headers": []},
         "response": {"content": {"mimeType": "image/png", "text": "zzz"}}},
    ]}}
    path = tmp_path / "c.har"
    path.write_text(json.dumps(har))
    return path


def test_har_extracts_only_matching_bodies_and_no_secrets(tmp_path):
    har = make_har(tmp_path)
    out = tmp_path / "out"
    code = har_to_fixtures.main([str(har), "--match", "Browse", "--html", "--out", str(out)])
    assert code == 0
    files = sorted(p.name for p in out.iterdir())
    assert len(files) == 2
    assert any(f.endswith(".json") and "Browse" in f for f in files)
    assert any(f.endswith(".html") for f in files)
    assert "supersecret" not in "".join(p.read_text() for p in out.iterdir())


def test_har_no_matches_returns_nonzero(tmp_path):
    har = make_har(tmp_path)
    assert har_to_fixtures.main([str(har), "--match", "nomatch", "--out", str(tmp_path / "o")]) == 1
