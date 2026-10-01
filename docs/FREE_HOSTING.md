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

## Choose a host (both are free; you can use either or both)

| | Cloudflare Pages (recommended) | GitHub Pages |
|---|---|---|
| Private code repo | yes (it uploads files directly, no repo needed) | **no**: the published repo must be public on the free plan (keep your code in a separate private repo) |
| Limits (check current docs) | 20,000 files per site, 25 MiB per file, generous bandwidth | about 1 GB site, soft bandwidth cap |
| Our site | ~4,000 files at most, files are small | fine |
| Uses | `scripts/deploy_cloudflare.sh` | `scripts/publish_pages.sh` |

### Cloudflare Pages

1. Make a free Cloudflare account. Install Node.js if you don't have it (`npx` is used; nothing else to install).
2. Log in once: `npx wrangler@3 login` (opens a browser). For the unattended 03:00 job, a login can expire; the more reliable way is an API token: Cloudflare dashboard, My Profile, API Tokens, "Create Token", permission **Account / Cloudflare Pages / Edit**. Note your Account ID (dashboard sidebar).
3. Settings file (private to you):

```bash
mkdir -p ~/.config/beeroo && cat >> ~/.config/beeroo/env <<'EOF'
BEEROO_CF_PROJECT=beeroo                 # lowercase letters, digits, hyphens; becomes beeroo.pages.dev
CLOUDFLARE_API_TOKEN=...                 # only for unattended runs
CLOUDFLARE_ACCOUNT_ID=...
EOF
chmod 600 ~/.config/beeroo/env
```

4. Try it by hand: `.venv/bin/python scripts/export_static.py && BEEROO_CF_PROJECT=beeroo scripts/deploy_cloudflare.sh` (the first run creates the project). The script refuses to upload if the site exceeds the free-tier limits.
5. From then on `scripts/daily_run.sh` deploys automatically after each scrape. Your address is `https://<project>.pages.dev`; a custom domain can be added in the Cloudflare dashboard.

### GitHub Pages

1. Create a new, *separate*, public repo (e.g. `beeroo-site`). Settings, Pages, deploy from branch `gh-pages`, root.
2. Put `BEEROO_PAGES_REMOTE=git@github.com:YOU/beeroo-site.git` in `~/.config/beeroo/env` (`chmod 600`). `scripts/publish_pages.sh` force-pushes a single commit each time, so the repo never grows.

### Both, or neither

Set both `BEEROO_CF_PROJECT` and `BEEROO_PAGES_REMOTE` to publish to both. Set neither and the daily job still exports to `site/` and says it didn't publish.

## Before the first publish: try it by hand

```bash
.venv/bin/python scripts/scheduled_scrape.py --zone NSW --retailer bws    # one state, one site
.venv/bin/python scripts/export_static.py                                 # writes ./site
python3 -m http.server -d site 8000                                       # look at it locally
```

## Schedule it

After that works: `scripts/install_launchd.sh --print` to review, then `scripts/install_launchd.sh` (daily 03:00). It runs `scripts/daily_run.sh`: scrape, export, freshness report, publish. If your Mac is asleep at the time it runs on wake; to have it wake for the run: `sudo pmset repeat wakeorpoweron MTWRFSU 02:55:00`. (macOS may need Full Disk Access for `/bin/sh` if the repo is under `~/Documents`.)

## Size

The export is a few thousand small files: roughly 6 MB per state with data, so up to ~50 MB when all eight have data. Free hosts allow this; each publish replaces it with a single commit so the repo doesn't grow.

## Before you publish publicly

- The site republishes retailers' prices. **Their terms of use are still unreviewed**; some forbid reproducing prices. Please read them, and consider keeping the site private (Cloudflare Pages access policies, or just the local `http.server`) until you have.
- The site shows "updated N days ago" and links to each retailer. A database built by `build_demo_db.py` is **simulated demo data**: never publish it.
- Free-tier limits change; check GitHub's/Cloudflare's current limits.
