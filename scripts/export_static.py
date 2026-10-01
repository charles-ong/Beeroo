"""Export the database as a FREE static website (no server needed).

    python scripts/export_static.py --db data/beeroo.sqlite3 --out site \\
        [--states NSW ACT VIC WA]

Writes site/index.html + static assets + data files:
  data/manifest.json                    which states are covered, when generated
  data/<ST>/<member>-<pack>.json        all products for a state (8 variants)
  data/<ST>/p/<productId>.json          product detail + price history

Host the folder on any static host (GitHub Pages, Cloudflare Pages, Netlify).
See docs/FREE_HOSTING.md.
"""
import argparse
import json
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app import queries  # noqa: E402
from common import db  # noqa: E402

REPRESENTATIVE_POSTCODE = {
    "NSW": "2000", "ACT": "2600", "VIC": "3000", "QLD": "4000",
    "SA": "5000", "WA": "6000", "TAS": "7000", "NT": "0800",
}
PACKS = [None, "single", "pack", "case"]
DEFAULT_STATES = ["NSW", "ACT", "VIC", "WA"]


def _write(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, separators=(",", ":"), sort_keys=True), encoding="utf-8")


def export(conn, out_dir, states=DEFAULT_STATES, now=None):
    now = now or datetime.now(timezone.utc)
    out = Path(out_dir)
    demo = db.get_meta(conn, "demo") == "1"
    written = {"files": 0, "bytes": 0}

    def write(path, data):
        _write(path, data)
        written["files"] += 1
        written["bytes"] += path.stat().st_size

    covered = []
    for state in states:
        postcode = REPRESENTATIVE_POSTCODE[state]
        locations = queries.resolve_locations(conn, postcode)
        latest = [l["latest"] for l in locations["retailers"].values() if l]
        if not latest:
            continue  # nothing loaded at all
        covered.append(state)
        meta = {"demo": demo, "latest_data": max(latest), "state": state,
                "generated_at": now.isoformat()}

        for include_member in (False, True):
            for pack in PACKS:
                products = queries.load_products(conn, locations, include_member, pack, now)
                write(out / queries_path(state, include_member, pack),
                      {"meta": meta, "locations": locations, "products": products})

        ids = {p["id"] for p in queries.load_products(conn, locations, True, None, now)}
        for pid in sorted(ids):
            detail = queries.product_detail(conn, pid, postcode, now)
            if detail:
                write(out / "data" / state / "p" / f"{pid}.json", detail)

    write(out / "data" / "manifest.json", {
        "generated_at": now.isoformat(), "states": covered, "demo": demo,
        "all_states": states,
    })
    return {"states": covered, **written}


def queries_path(state, include_member, pack):
    return Path("data") / state / f"{1 if include_member else 0}-{pack or 'any'}.json"


def copy_site(out_dir):
    out = Path(out_dir)
    static = ROOT / "app" / "static"
    (out / "static").mkdir(parents=True, exist_ok=True)
    for name in ("style.css", "app.js", "staticdata.js"):
        shutil.copy(static / name, out / "static" / name)
    html = (static / "index.html").read_text(encoding="utf-8")
    html = html.replace("<head>", '<head>\n<meta name="beeroo-mode" content="static">', 1)
    (out / "index.html").write_text(html, encoding="utf-8")
    (out / ".nojekyll").write_text("")  # GitHub Pages: serve files as-is


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--db", default="data/beeroo.sqlite3")
    ap.add_argument("--out", default="site")
    ap.add_argument("--states", nargs="+", default=DEFAULT_STATES, choices=sorted(REPRESENTATIVE_POSTCODE))
    args = ap.parse_args()

    if not Path(args.db).exists():
        sys.exit(f"no such database: {args.db}")
    out = Path(args.out)
    if out.exists():
        shutil.rmtree(out)
    conn = db.connect(args.db)
    stats = export(conn, out, args.states)
    copy_site(out)
    print(f"exported states {stats['states']}: {stats['files']} data files, {stats['bytes'] / 1e6:.1f} MB -> {out}")


if __name__ == "__main__":
    main()
