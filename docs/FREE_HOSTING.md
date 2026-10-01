# Free setup (no paid hosting)

Everything runs on **your own computer**; the public site is just static files on a free host. Nothing here costs money.

```
 your Mac, once a day (launchd):
   scrape BWS + Dan Murphy's (Sydney, Canberra, Melbourne, Perth) ─┐
   Liquorland: refreshed by hand in your own Chrome (extension)    ├─► local SQLite DB
                                                                   ┘
   export_static.py ─► site/ (HTML + JSON files) ─► publish_pages.sh ─► free static host
                                                                       (GitHub Pages / Cloudflare Pages)
```

## What you get, and what you give up

| | Free static site | Paid server (optional, `docs/DEPLOYMENT.md`) |
|---|---|---|
| Cost | $0 | a few dollars a month |
| Compare / filter / sort / price history | yes (computed in the visitor's browser) | yes |
| Covers | NSW, ACT, VIC, WA (the four cities); other states show a clear "not covered" message | any state with data |
| Public crowd contributions (extension) | **no** (needs a server) | yes, if you open it |
| Freshness | as of your last daily run | same |

The extension still works for **you**: point it at your own local server (below) and it feeds Liquorland/BWS data into your database.

## One-time setup

1. **Make a site repo.** Create a new, *separate*, public repo on GitHub (e.g. `beeroo-site`), keeping your code private. Enable Pages: Settings → Pages → deploy from branch `gh-pages`, root. (GitHub Pages on the free plan requires a public repo. Cloudflare Pages is a free alternative that works with private repos: connect the repo or use "Direct Upload" of the `site/` folder.)
2. **Settings file** (private to you):

```bash
mkdir -p ~/.config/beeroo && cat > ~/.config/beeroo/env <<'EOF'
BEEROO_PAGES_REMOTE=git@github.com:YOU/beeroo-site.git
BEEROO_ZONES_PER_RUN=1
EOF
chmod 600 ~/.config/beeroo/env
```

3. **Try it by hand first:**

```bash
.venv/bin/python scripts/scheduled_scrape.py --retailer bws --zone 2600   # one city, watch the window
.venv/bin/python scripts/export_static.py                                 # writes ./site
python3 -m http.server -d site 8000                                       # look at it locally
scripts/publish_pages.sh site                                             # publish (force-pushes ONE commit)
```

4. **Schedule it** (after step 3 works): `scripts/install_launchd.sh --print` to review, then `scripts/install_launchd.sh --hour 11 --minute 30`. Runs `scripts/daily_run.sh`: scrape → export → freshness report → publish. If your Mac is asleep it runs on wake.

## How often is each city refreshed?

`BEEROO_ZONES_PER_RUN=1` (default): one city per retailer per day, so each city refreshes every 4 days. `=4` refreshes all four daily but means ~8 browser sessions a day; BWS tolerated this in testing, Dan Murphy's did not tolerate far less. Start with 1.

## Using the extension against your own copy

```bash
BEEROO_QUORUM=1 BEEROO_DB=data/beeroo.sqlite3 .venv/bin/uvicorn app.main:app --no-access-log
```

`BEEROO_QUORUM=1` is single-user mode (your own contributions are trusted immediately; without it a lone contributor can't reach the 2-person quorum). Keep the extension's server at `http://127.0.0.1:8000`. The next daily run exports whatever it collected.

## Liquorland

Refreshed by hand: see `docs/LIQUORLAND_MANUAL.md`. `scripts/check_freshness.py` tells you what's due.

## Before you publish publicly

- The site republishes retailers' prices. **Their terms of use are still unreviewed**; some forbid reproducing prices. Please read them, and consider keeping the site private (Cloudflare Pages access policies, or just `python3 -m http.server` for yourself) until you have.
- The site is clearly labelled with "updated N days ago" and links to each retailer. Demo data is flagged with a banner; **never publish a database built by `build_demo_db.py`** (its history is simulated).
- Free-tier limits change; check GitHub's/Cloudflare's current limits. The export is ~1,300 small files per run.
