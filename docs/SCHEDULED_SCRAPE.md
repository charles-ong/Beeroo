# Scheduled scraping (BWS + Dan Murphy's, four cities)

Daily, on **your own Mac**, in a **visible browser window**, for Sydney (2000), Canberra (2600), Melbourne (3000) and Perth (6000). Liquorland is not automated (see `docs/LIQUORLAND_MANUAL.md`). This is part of the free setup in `docs/FREE_HOSTING.md`.

## Status (2026-10-01)

| Retailer | Live status |
|---|---|
| **BWS** | **Verified end to end** (Canberra 2600): robots.txt allows category pages; store picker works; all 745 beers loaded via "Load more" in ~3.5 min; 217 in-stock products and 617 prices stored. Not blocked. Other cities should behave the same but haven't been run. |
| **Dan Murphy's** | Scraper built and tested with fakes, **blocked** by Cloudflare on this IP (first request, ~2.5 h after an earlier block). Its store-selection steps have never run live. Backoff logic worked on the real block. |

Retailer terms of use are still **unreviewed**; automated access may breach them.

## What one run does

For each retailer (BWS first), pick the least-recently-updated city (or `--zones-per-run N` cities, 4 minutes apart on average):

1. BWS: load `robots.txt` in the same visible browser and **stop if the target path is disallowed**.
2. Open the beer page, pick the nearest store through the site's own store modal.
3. Click "Load more" like a user (2-4 s between clicks) until the list ends.
4. Read the product data the page loads (no extra requests); keep only in-stock items and the fields we need (12 MB becomes 0.3 MB); store it locally or push it.

BWS lists ~745 beers but a single store only stocks about 200-300; the rest are counted as "accounted for", not as missing.

## Safety behaviour (tested)

- A block page: that retailer stops at once, never retries that day, backs off 2, 4, 8, up to 14 days. Each retailer backs off independently, so a Dan Murphy's block doesn't stop BWS.
- robots.txt disallows the page: skip that retailer for 7 days.
- Backoff is checked **before** a browser is launched. Another run in progress: exits.
- A result missing more than 20% (after counting out-of-stock) is saved but not marked fresh.
- No proxies, stealth plugins, CAPTCHA solving or IP rotation, ever. If a site blocks us we stop.

## Run it

```bash
.venv/bin/python scripts/scheduled_scrape.py --retailer bws --zone 2600     # try one city by hand
.venv/bin/python scripts/scheduled_scrape.py                                 # both retailers, 1 city each
.venv/bin/python scripts/scheduled_scrape.py --zones-per-run 4 --retailer bws
```

Results go to `data/beeroo.sqlite3` (or `--push` to a server). State and backoff: `data/scrape_state.json` (delete a retailer's `blocked` entry to clear a backoff); logs: `data/scrape.log`.

## Schedule it (not installed for you)

```bash
scripts/install_launchd.sh --print                # review what would be installed
scripts/install_launchd.sh --hour 11 --minute 30  # install; runs scripts/daily_run.sh
scripts/install_launchd.sh --uninstall
```

`daily_run.sh` = scrape, then export the static site, then print a freshness report, then publish (if `BEEROO_PAGES_REMOTE` is set). A visible browser window will appear for a few minutes. A LaunchAgent needs you logged in; if the Mac sleeps it runs on wake.

## If a retailer keeps blocking

Stop. Don't try to defeat the block. Use manual captures (`docs/CAPTURE_GUIDE.md`), the extension, or ask the retailer for a feed.
