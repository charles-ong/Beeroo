"""Turn a browser HAR export into response-body fixtures.

Why: lets us build/verify a retailer parser from pages you browsed yourself,
with no automated requests to the retailer. Only response *bodies* are
written; request headers, cookies and tokens in the HAR are never copied.

    python scripts/har_to_fixtures.py capture.har --list
    python scripts/har_to_fixtures.py capture.har --match Browse --match /api/ --out tests/fixtures/liquorland
    python scripts/har_to_fixtures.py capture.har --html --out tests/fixtures/liquorland

How to capture: see docs/CAPTURE_GUIDE.md.
"""
import argparse
import base64
import json
import re
import sys
from pathlib import Path
from urllib.parse import urlsplit


def entries(har):
    return har.get("log", {}).get("entries", [])


def body_of(entry):
    content = entry.get("response", {}).get("content", {})
    text = content.get("text")

    if text is None:
        return None

    if content.get("encoding") == "base64":
        return base64.b64decode(text)

    return text.encode("utf-8")


def mime_of(entry):
    return (
        entry.get("response", {}).get("content", {}).get("mimeType") or ""
    ).lower()


def slug(url, index):
    parts = urlsplit(url)
    raw = f"{parts.netloc}{parts.path}".strip("/")
    raw = re.sub(r"[^A-Za-z0-9]+", "_", raw)[:80].strip("_")
    return f"{index:03d}_{raw}"


def is_candidate(entry, matches, want_html):
    url = entry["request"]["url"]
    mime = mime_of(entry)

    if want_html and "html" in mime and entry["request"]["method"] == "GET":
        return True

    if "json" not in mime:
        return False

    return not matches or any(m.lower() in url.lower() for m in matches)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("har")
    ap.add_argument("--list", action="store_true", help="list JSON/HTML responses and exit")
    ap.add_argument("--match", action="append", default=[], help="keep JSON responses whose URL contains this (repeatable)")
    ap.add_argument("--html", action="store_true", help="also keep HTML documents")
    ap.add_argument("--out", default="tests/fixtures/capture")
    args = ap.parse_args(argv)

    har = json.loads(Path(args.har).read_text(encoding="utf-8"))
    rows = entries(har)

    if args.list:
        for i, e in enumerate(rows):
            mime = mime_of(e)
            if "json" in mime or "html" in mime:
                size = len(body_of(e) or b"")
                print(f"{i:03d} {e['request']['method']:4} {size:>9}  {e['request']['url'][:140]}")
        return 0

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    written = 0

    for i, e in enumerate(rows):
        if not is_candidate(e, args.match, args.html):
            continue

        body = body_of(e)

        if not body:
            continue

        ext = "html" if "html" in mime_of(e) else "json"
        path = out / f"{slug(e['request']['url'], i)}.{ext}"
        path.write_bytes(body)
        written += 1
        print(f"wrote {path} ({len(body)} bytes) <- {e['request']['method']} {e['request']['url'][:100]}")

    print(f"{written} file(s) written to {out}")
    return 0 if written else 1


if __name__ == "__main__":
    sys.exit(main())
