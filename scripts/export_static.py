"""Export the database as a FREE static website (no server needed).

    python scripts/export_static.py --db data/beeroo.sqlite3 --out site \\
        [--states NSW ACT ...]      # default: every state and territory

Writes site/index.html + static assets + data files:
  data/manifest.json                    which states are covered, when generated
  data/<ST>/products.json               every product and price option for a state (the browser filters)
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
from common.states import STATES, state_list  # noqa: E402

DEFAULT_STATES = list(STATES)


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
        locations = queries.resolve_locations(conn, state)
        latest = [l["latest"] for l in locations["retailers"].values() if l]
        # Only states with data of their own are exported; the dropdown marks the
        # others "(no prices yet)". There is no cross-state fallback.
        if not latest:
            continue
        covered.append(state)
        products = queries.load_products(conn, locations, now)
        meta = {"demo": demo, "latest_data": max(latest), "state": state,
                "generated_at": now.isoformat(), "facets": queries.facets(products)}
        write(out / queries_path(state), {"meta": meta, "locations": locations, "products": products})

        ids = {p["id"] for p in products}
        for pid in sorted(ids):
            detail = queries.product_detail(conn, pid, state, now)
            if detail:
                write(out / "data" / state / "p" / f"{pid}.json", detail)

    write(out / "data" / "manifest.json", {
        "generated_at": now.isoformat(), "states": covered, "demo": demo,
        "all_states": [s for s in state_list() if s["code"] in states],
    })
    return {"states": covered, **written}


def queries_path(state):
    return Path("data") / state / "products.json"


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
    ap.add_argument("--states", nargs="+", default=DEFAULT_STATES, choices=sorted(STATES))
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
