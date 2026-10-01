# Scheduled scraping (BWS, Liquorland, Dan Murphy's; every state and territory)

Daily, on **your own computer**, in a **visible browser window**. Each state/territory is priced from the store nearest its pricing postcode (`common/states.py`, e.g. NSW 2100). This is part of the free setup in `docs/FREE_HOSTING.md`.

## Status (2026-10-01)

| Retailer | Live status |
|---|---|
| **BWS** | **Verified live** for ACT (2600) and NSW (2100): robots.txt allows category pages; store picker works; all 745 beers load via "Load more"; ~190-220 in-stock products per store. Not blocked. |
| **Liquorland** | **Verified live** for NSW (2100): no CAPTCHA appeared; location modal + nearest-store selection work; 13 pages at 80 per page gave all 987 entries (392 products). **Caveat:** its `robots.txt` disallows `/api/*`, the path its pages load this data from. It is included because you asked for all sites. Switch it off with `BEEROO_SKIP_RETAILERS=liquorland`. |
| **Dan Murphy's** | Built and tested with fakes, but **blocked** by Cloudflare from the development machine (even a single page load, hours after earlier blocks). Its store-selection steps have never run live. The daily run still tries it once per state and backs off. |

The other states have not been run live. Retailer terms of use are **unreviewed**; automated access may breach them.

## What one daily run does

For every state/territory, for each retailer, **interleaved** (BWS NSW, Liquorland NSW, Dan Murphy's NSW, BWS ACT, ...) so no site sees back-to-back sessions (a minimum average gap of 3 minutes per site is enforced on top):

1. Open the beer page, set the state's pricing postcode through the site's own location picker, and choose the **nearest** store.
2. Page through the list like a user ("Load more" at BWS; 80 per page then "next page" at Liquorland).
3. Read only the product data the page loads (no extra requests); keep in-stock items and the fields we need; store it locally or push it.

A full run (3 sites x 8 states) takes roughly 1.5-2 hours with windows opening and closing, which is why the default schedule is 03:00.

## Safety behaviour (tested)

- **Bot protection or a CAPTCHA** (Cloudflare "Attention Required", HTTP 403/429, Liquorland's ShieldSquare/perfdrive page): that retailer stops for the rest of the run, backs off 2, 4, 8, up to 14 days, and is never retried the same day. **We never try to solve a CAPTCHA or get past a block.** Retailers back off independently.
- **robots.txt** is read for BWS and Liquorland pages in the same browser; a disallowed *page* path means skipping that retailer for 7 days.
- Backoff is checked **before** a browser is launched. Another run in progress: exits.
- A result missing more than 20% of the site's total (after counting out-of-stock items) is saved but not marked fresh.
- No proxies, stealth plugins, CAPTCHA solving or IP rotation. A test fails if such code is added.

## Run it

```bash
.venv/bin/python scripts/scheduled_scrape.py --zone NSW --retailer bws     # one state, one site (try this first)
.venv/bin/python scripts/scheduled_scrape.py                                 # everything: 3 sites x 8 states
.venv/bin/python scripts/scheduled_scrape.py --zones-per-run 2               # only the 2 least-recently-updated states
BEEROO_SKIP_RETAILERS=liquorland .venv/bin/python scripts/scheduled_scrape.py
```

Results go to `data/beeroo.sqlite3` (or `--push` to a server). State and backoff: `data/scrape_state.json` (delete a retailer's `blocked` entry to clear a backoff); logs: `data/scrape.log`. `scripts/check_freshness.py` lists every state/retailer and how old its data is.

## Schedule it (not installed for you)

```bash
scripts/install_launchd.sh --print     # review what would be installed
scripts/install_launchd.sh             # daily 03:00; --hour/--minute to change
scripts/install_launchd.sh --uninstall
```

`daily_run.sh` = scrape, export the static site, freshness report, publish (if `BEEROO_PAGES_REMOTE` is set). A LaunchAgent needs you logged in; if the Mac sleeps it runs on wake.

> **Update:** the experiment succeeded for BWS and Liquorland, and the daily cloud job now exists: see [CLOUD_SCRAPE.md](CLOUD_SCRAPE.md). With `BEEROO_DATA_REMOTE` set, this machine scrapes Dan Murphy's only.

## Can the scraping move to the cloud? (experiment)

Maybe, for BWS and Liquorland; probably not for Dan Murphy's. Retailers often block datacenter addresses, so don't assume: test first. `.github/workflows/cloud-scrape-experiment.yml` runs **one retailer for one state** on a free GitHub-hosted runner (real Chromium on a virtual display) and writes a plain verdict to the run summary: *works*, *blocked*, *skipped by robots.txt* or *error*.

1. Push this repo to GitHub (a **private** repo is fine; public repos get free minutes without limit, private ones a monthly allowance; check GitHub's current terms).
2. Actions tab, **Cloud scrape experiment**, **Run workflow**, pick a retailer and state (default BWS, NSW).
3. Read the **Summary** on the run page; the log, result and any screenshots are attached as an artifact for 7 days.

If BWS says *works*, you can enable the commented-out `schedule:` in the workflow, extend it to loop over states, and publish the result from the runner. That's a future step: the experiment deliberately stores nothing outside the run. If it says *blocked*, the answer is the home connection (an always-on spare machine is the natural upgrade). It never tries to get past a block.

## If a retailer keeps blocking

Stop. Don't try to defeat the block. Leave that retailer out (`BEEROO_SKIP_RETAILERS`) or ask it for a data feed. The site works with whatever retailers have data.
