# Free setup (no paid hosting)

Everything runs on **your own computer**; the public site is plain static files on a free host. Nothing here costs money.

```
 your computer, daily (launchd):
   scrape BWS, Liquorland, Dan Murphy's for every state/territory ──► local SQLite DB
   export_static.py ─► site/ (HTML + JSON files) ─► publish_pages.sh ─► free static host
                                                                       (GitHub Pages / Cloudflare Pages)
```

## What you get, and what you give up

| | Free static site | Paid server (optional, `docs/DEPLOYMENT.md`) |
|---|---|---|
| Cost | $0 | a few dollars a month |
| State dropdown, compare / filter / sort / price history | yes (computed in the visitor's browser) | yes |
| Covers | every state/territory that has data; others show "(no prices yet)" | same |
| Live API | no (files only) | yes |
| Freshness | as of your last daily run | same |

## One-time setup

1. **Make a site repo.** Create a new, *separate*, public repo on GitHub (e.g. `beeroo-site`), keeping your code private. Enable Pages: Settings, Pages, deploy from branch `gh-pages`, root. (On the free plan GitHub Pages needs a public repo. Cloudflare Pages is a free alternative that works with private repos: "Direct Upload" the `site/` folder.)
2. **Settings file** (private to you):

```bash
mkdir -p ~/.config/beeroo && cat > ~/.config/beeroo/env <<'EOF'
BEEROO_PAGES_REMOTE=git@github.com:YOU/beeroo-site.git
# BEEROO_SKIP_RETAILERS=liquorland      # leave a site out (comma-separated)
# BEEROO_ZONES_PER_RUN=8                # states per retailer per day (default 8 = all)
EOF
chmod 600 ~/.config/beeroo/env
```

3. **Try it by hand first** (one state, one site; a browser window opens):

```bash
.venv/bin/python scripts/scheduled_scrape.py --zone NSW --retailer bws
.venv/bin/python scripts/export_static.py                 # writes ./site
python3 -m http.server -d site 8000                       # look at it locally
scripts/publish_pages.sh site                             # publish (force-pushes ONE commit)
```

4. **Schedule it** (after step 3 works): `scripts/install_launchd.sh --print` to review, then `scripts/install_launchd.sh` (daily 03:00). It runs `scripts/daily_run.sh`: scrape, export, freshness report, publish. If your Mac is asleep at the time it runs on wake; to have it wake for the run: `sudo pmset repeat wakeorpoweron MTWRFSU 02:55:00`.

## Size

The export is a few thousand small files: roughly 6 MB per state with data, so up to ~50 MB when all eight have data. Free hosts allow this; each publish replaces it with a single commit so the repo doesn't grow.

## Before you publish publicly

- The site republishes retailers' prices. **Their terms of use are still unreviewed**; some forbid reproducing prices. Please read them, and consider keeping the site private (Cloudflare Pages access policies, or just the local `http.server`) until you have.
- The site shows "updated N days ago" and links to each retailer. A database built by `build_demo_db.py` is **simulated demo data**: never publish it.
- Free-tier limits change; check GitHub's/Cloudflare's current limits.
