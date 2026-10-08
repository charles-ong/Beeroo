"""Import Dan Murphy's prices from a page YOU browsed and saved by hand.

No automated visit to their site is involved, so bot protection isn't. You open the
beer page in your normal browser, load every product, save it, and run this.

    python scripts/import_dm_page.py ~/Downloads/dm-nsw.json --state NSW
    python scripts/import_dm_page.py ~/Downloads/Beer.html  --state VIC

Input is either
  * JSON from the console snippet in docs/DAN_MURPHYS_MANUAL.md (most reliable), or
  * the page saved with "Webpage, Complete" (an .html file).

Set the store on danmurphys.com.au to the state's pricing postcode first (see the
docs); the state you pass here is what the prices are filed under.

Where it goes: if BEEROO_DATA_REMOTE is set (in ~/.config/beeroo/env) the pages go to
the cloud job's inbox, like your nightly Dan Murphy's run; otherwise (or with --local)
straight into data/beeroo.sqlite3.
"""
import argparse
import json
import os
import re
import stat
import subprocess
import sys
from datetime import datetime, timezone
from html.parser import HTMLParser
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

from app import ingest  # noqa: E402
from common.states import STATES, normalise_state  # noqa: E402
from scrapers import dan_murphys_dom as dom  # noqa: E402

MIN_CARDS = 40          # a full beer category is several hundred; fewer means "Load more" wasn't finished
ENV_FILE = Path.home() / ".config" / "beeroo" / "env"
OUTBOX = ROOT / "data" / "outbox"
DEFAULT_DB = ROOT / "data" / "beeroo.sqlite3"


class ImportProblem(Exception):
    pass


# --------------------------------------------------------------------------
# Reading the saved page
# --------------------------------------------------------------------------


class _CardParser(HTMLParser):
    """Collects each <shop-product-card>: its first product link and its text
    nodes, one per line (like a text dump of the page)."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.cards, self._card, self._depth, self._skip = [], None, 0, 0

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag == "shop-product-card":
            if self._card is None:
                self._card = {"href": a.get("data-url") or "", "lines": []}
            self._depth += 1
        if self._card is None:
            return
        if tag in ("script", "style", "noscript"):
            self._skip += 1
        if tag == "a" and "/product/" in (a.get("href") or "") and not self._card.get("_linked"):
            self._card["href"], self._card["_linked"] = a["href"], True

    def handle_endtag(self, tag):
        if self._card is None:
            return
        if tag in ("script", "style", "noscript") and self._skip:
            self._skip -= 1
        if tag == "shop-product-card":
            self._depth -= 1
            if self._depth == 0:
                self._card.pop("_linked", None)
                self.cards.append(self._card)
                self._card = None

    def handle_data(self, data):
        if self._card is not None and not self._skip and data.strip():
            self._card["lines"].append(data.strip())


def cards_from_html(text):
    parser = _CardParser()
    parser.feed(text)
    parser.close()
    return parser.cards


def cards_from_json(text):
    data = json.loads(text)
    cards = data.get("cards") if isinstance(data, dict) else data
    if not isinstance(cards, list):
        raise ImportProblem("the JSON has no list of cards (expected the snippet's output)")
    return cards


def unrtf(text):
    """The plain text of a TextEdit Rich Text file (what you get by pasting into TextEdit and saving).
    Only the escapes TextEdit writes are handled; anything else is left alone."""
    start = re.search(r"\\cf0\s", text)
    body = text[start.end():] if start else text
    if "}" in body:
        body = body[: body.rindex("}")]                   # the RTF document's closing brace
    out, i, fallback = [], 0, 1
    while i < len(body):
        c = body[i]
        if c != "\\":
            out.append(c)
            i += 1
            continue
        nxt = body[i + 1: i + 2]
        if nxt == "'" and re.fullmatch(r"[0-9a-fA-F]{2}", body[i + 2: i + 4]):
            out.append(bytes([int(body[i + 2: i + 4], 16)]).decode("cp1252", "replace"))
            i += 4
        elif nxt in ("{", "}", "\\"):
            out.append(nxt)
            i += 2
        elif nxt == "\n":
            out.append("\n")
            i += 2
        elif (m := re.match(r"\\uc(\d+) ?", body[i:])):
            fallback = int(m.group(1))                    # characters to skip after each \uN
            i += m.end()
        elif (m := re.match(r"\\u(-?\d+) ?", body[i:])):
            n = int(m.group(1))
            out.append(chr(n if n >= 0 else n + 65536))
            i += m.end() + fallback
        elif (m := re.match(r"\\(par|line)\b ?", body[i:])):
            out.append("\n")
            i += m.end()
        elif (m := re.match(r"\\tab\b ?", body[i:])):
            out.append("\t")
            i += m.end()
        else:
            i += 1
    result = "".join(out).strip()
    # TextEdit's "smart quotes" turn the first straight quote into a curly one
    return re.sub(r'^([{\[])[\u201c\u201d]', r'\1"', result)


class _TextOnly(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts, self._skip = [], 0

    def handle_starttag(self, tag, attrs):
        self._skip += tag in ("style", "script", "title", "head")

    def handle_endtag(self, tag):
        if tag in ("style", "script", "title", "head") and self._skip:
            self._skip -= 1

    def handle_data(self, data):
        if not self._skip:
            self.parts.append(data)


def text_of_html(text):
    parser = _TextOnly()
    parser.feed(text)
    return "".join(parser.parts).strip()


def read_clipboard():
    """The clipboard's text (macOS pbpaste)."""
    try:
        proc = subprocess.run(["pbpaste"], capture_output=True, text=True, timeout=15)
    except (OSError, subprocess.TimeoutExpired):
        raise ImportProblem("cannot read the clipboard here (macOS only): save the snippet's output to a "
                            "plain-text .json file instead")
    if not proc.stdout.strip():
        raise ImportProblem("the clipboard is empty: run the console snippet first")
    return cards_from_text(proc.stdout, "the clipboard")


def read_cards(path):
    path = Path(path)
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError as e:
        raise ImportProblem(f"cannot read {path}: {e}")
    return cards_from_text(text, str(path))


def cards_from_text(text, source="the input"):
    """JSON from the snippet (also when a text editor wrapped it as Rich Text or HTML), or a saved page."""
    if text.lstrip().startswith("{\\rtf"):
        text = unrtf(text)
    elif "<" in text and not text.lstrip().startswith(("{", "[")):
        cards = cards_from_html(text)
        if cards:
            return cards
        plain = text_of_html(text)                         # JSON pasted into a text editor and saved as HTML
        if plain.startswith(("{", "[", "{\u201c")):
            text = re.sub(r'^([{\[])[\u201c\u201d]', r'\1"', plain)
        else:
            raise ImportProblem(f"{source} has no product cards. Browsers usually save a page's original source, "
                                "not the products you see loaded. Use the console snippet and --clipboard instead.")
    if text.lstrip().startswith(("{", "[")):
        try:
            return cards_from_json(text)
        except ValueError as e:
            raise ImportProblem(f"{source} is not valid JSON: {e}. If you pasted it into a text editor, "
                                "it may have changed the quotes: use --clipboard instead.")
    cards = cards_from_html(text)
    if not cards:
        raise ImportProblem(f"{source} has no product cards. Use the console snippet and --clipboard instead.")
    return cards


# --------------------------------------------------------------------------
# Checking and sending
# --------------------------------------------------------------------------


def build_body(cards, state, observed_at, store=None):
    code = normalise_state(state)
    if code is None:
        raise ImportProblem(f"--state must be one of {', '.join(STATES)}")
    store = store or {}
    return {
        "kind": "dan_murphys_cards",
        "location": {
            "store_id": store.get("store_id") or f"manual_{code.lower()}",
            "store_name": store.get("store_name") or "Dan Murphy's (imported by hand)",
            "suburb": store.get("suburb"),
            "state": code,
            "postcode": STATES[code]["postcode"],
        },
        "payload": {"cards": cards},
        "observed_at": observed_at.isoformat(),
    }


def check(body, min_cards=MIN_CARDS, force=False):
    """Parse exactly as the server will. Returns (products, errors, unique_cards);
    raises ImportProblem for anything the server would reject or that looks incomplete."""
    cards = body["payload"]["cards"]
    unique = dom.count_unique(cards)
    if not unique:
        raise ImportProblem("no product cards found: was this the beer category page, with products loaded?")
    try:
        loc = ingest.resolve_location("dan_murphys_cards", ingest.LocationIn.model_validate(body["location"]), body["payload"])
        products, errors = dom.parse_cards_payload(body["payload"], loc.location_key)
    except (ValueError, ingest.IngestError) as e:
        raise ImportProblem(f"the page could not be read: {getattr(e, 'message', e)}")
    if ingest.drift_share(errors, len(cards)) > ingest.DRIFT_ERROR_SHARE:
        sample = "; ".join(f"{i}: {why}" for i, why in errors[:3])
        raise ImportProblem(f"too many cards could not be read ({len(errors)} of {len(cards)}; {sample}). "
                           "The page layout may have changed.")
    if not products:
        raise ImportProblem("no products with online prices were found")
    if unique < min_cards and not force:
        raise ImportProblem(f"only {unique} products found (a full category has several hundred). Keep clicking "
                           f"'Load more' until it disappears, then save again, or pass --force.")
    return products, errors, unique


def load_env_file(path=ENV_FILE, environ=os.environ):
    """KEY=VALUE lines into the environment (like scripts/daily_run.sh); the file must be private."""
    path = Path(path)
    if not path.exists():
        return
    if path.stat().st_mode & (stat.S_IRWXG | stat.S_IRWXO):
        raise ImportProblem(f"refusing to read {path}: run chmod 600 {path}")
    for line in path.read_text().splitlines():
        m = re.fullmatch(r"\s*(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)=(.*)", line)
        if m and not line.lstrip().startswith("#"):
            environ.setdefault(m.group(1), m.group(2).strip().strip("'\""))


def send(body, local=False, db_path=DEFAULT_DB, outbox=OUTBOX, environ=os.environ):
    """Returns a one-line description of where the page went."""
    import data_branch
    import scheduled_scrape

    remote = environ.get("BEEROO_DATA_REMOTE", "").strip()
    if local or not remote:
        result = scheduled_scrape.local_push(db_path)(body)
        return f"saved to {db_path}: {result['products']} products, {result['new_observations']} new price observations"

    scheduled_scrape.emit_push(outbox)(body)
    try:
        data_branch.push_inbox(remote, outbox, "dan_murphys")
    except data_branch.DataBranchError as e:
        return f"saved to {outbox} but could not reach the cloud inbox ({e}); the nightly run will retry"
    return "sent to the cloud inbox; the next daily cloud run will add it to the database and the site"


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("file", nargs="?", help="the console-snippet JSON saved as plain text (or --clipboard instead)")
    ap.add_argument("--clipboard", action="store_true", help="read what the console snippet copied (macOS): no file needed")
    ap.add_argument("--state", required=True, help=f"which state/territory the store is set to ({', '.join(STATES)})")
    ap.add_argument("--store-id", help="the store's id, if you know it (default: manual_<state>)")
    ap.add_argument("--store-name")
    ap.add_argument("--suburb")
    ap.add_argument("--observed-at", help="ISO 8601; default: when the file was saved")
    ap.add_argument("--local", action="store_true", help="write to data/beeroo.sqlite3 even if a cloud inbox is set up")
    ap.add_argument("--dry-run", action="store_true", help="check the file and show what would be imported; send nothing")
    ap.add_argument("--force", action="store_true", help=f"import even if fewer than {MIN_CARDS} products were found")
    ap.add_argument("--db", default=str(DEFAULT_DB))
    args = ap.parse_args(argv)

    try:
        load_env_file()
        if bool(args.file) == args.clipboard:
            raise ImportProblem("give either a file or --clipboard")
        observed = (datetime.fromisoformat(args.observed_at) if args.observed_at
                    else datetime.now(timezone.utc) if args.clipboard
                    else datetime.fromtimestamp(Path(args.file).stat().st_mtime, timezone.utc))
        body = build_body(read_clipboard() if args.clipboard else read_cards(args.file), args.state, observed,
                          {"store_id": args.store_id, "store_name": args.store_name, "suburb": args.suburb})
        products, errors, unique = check(body, force=args.force)
        prices = sum(len(p.prices) for p in products)
        print(f"{unique} products found, {len(products)} with online prices ({prices} price options), "
              f"{len(errors)} skipped; filed under {body['location']['state']}, observed {observed:%Y-%m-%d %H:%M} UTC")
        print(f"Check: the site must have been showing a {body['location']['state']} store when you copied this. "
              "Dan Murphy's prices and range differ by state; identical files for two states mean the store wasn't changed.")
        if args.dry_run:
            print("dry run: nothing sent")
            return 0
        print(send(body, local=args.local, db_path=args.db))
    except (ImportProblem, OSError, ValueError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
