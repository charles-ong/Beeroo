# Daily scraping in the cloud (BWS + Liquorland) with your Mac doing Dan Murphy's

```
 GitHub Actions, daily 19:00 UTC (free runner)            your Mac, daily 03:00 (launchd)
   restore DB from the `data` branch                        scrape Dan Murphy's only (visible browser)
   ingest Dan Murphy's pages from the branch inbox  <────── push the pages to `data` branch inbox/
   scrape BWS + Liquorland, all 8 states
   save DB back to the `data` branch
   export site ─► deploy to Cloudflare Pages
```

Dan Murphy's blocks cloud servers (and headless browsers), so only your Mac scrapes it. Everything else
runs without your Mac. If your Mac is off, the site simply keeps yesterday's Dan Murphy's prices and the
freshness report says so. Cost: $0 (check GitHub's and Cloudflare's current free allowances).

## One-time setup

1. **Push the repo to GitHub** (`origin` is already `charles-ong/Beeroo`). The workflow lives in
   `.github/workflows/daily-scrape.yml`.
2. **Seed the cloud database with your existing price history**, from this folder:
   ```bash
   BEEROO_DATA_REMOTE=git@github.com:charles-ong/Beeroo.git .venv/bin/python scripts/data_branch.py seed --db data/beeroo.sqlite3
   ```
   It creates the `data` branch (one commit: the database). It refuses to overwrite an existing one; if a cloud
   run already saved a thinner database before you seeded, add `--replace` to replace it with your local history.
   Skip this and the first cloud run starts an empty history.
3. **Cloudflare**: create a Pages API token ("Cloudflare Pages: Edit") and find your account ID. In the repo:
   Settings → Secrets and variables → Actions →
   Secrets `CLOUDFLARE_API_TOKEN`, `CLOUDFLARE_ACCOUNT_ID`; Variable `BEEROO_CF_PROJECT` (e.g. `beeroo`).
   Without the variable the job still scrapes and saves; it just doesn't deploy.
4. **Allow the workflow to write**: Settings → Actions → General → Workflow permissions can stay
   "Read repository contents" because the workflow asks for `contents: write` itself, but an organisation
   policy or branch protection on `data` can block it. Don't protect the `data` branch.
5. **Switch your Mac to Dan Murphy's only**: put this in `~/.config/beeroo/env` (`chmod 600` it):
   ```
   BEEROO_DATA_REMOTE=git@github.com:charles-ong/Beeroo.git
   ```
   With that set, `scripts/daily_run.sh` scrapes only Dan Murphy's, pushes the pages and stops: it no longer
   scrapes BWS/Liquorland, exports or publishes. The Mac needs git access to the repo (SSH key or the macOS
   keychain). launchd needs no change.
6. **Try it**: Actions → Daily cloud scrape → Run workflow. Read the run summary (a table per state, then freshness).

## Day to day

- The run **fails (red, emails you)** when a site errored or blocked us, but only after it saved the data and deployed.
- `data/outbox/` on the Mac holds pages that couldn't be pushed (offline); they go with the next run.
- A page the cloud can't ingest is moved to `inbox/rejected/` on the `data` branch (also in the run's artifact).
- After a block, that retailer is skipped for 2, 4, 8... days. To retry sooner: Actions → Run workflow → *clear_backoff*.
- Back-off state for the cloud scraper is saved too (`scrape_state_cloud.json`), so a block is respected across days.
- To drop Liquorland from the cloud job: change `BEEROO_SKIP_RETAILERS` in the workflow to `dan_murphys,liquorland`.
- To go back to everything on the Mac: remove `BEEROO_DATA_REMOTE` from the env file and disable the workflow.

## Things to know

- **The `data` branch is public if the repo is public**: it holds the price database and the raw Dan Murphy's pages.
  Prices are public information, but check the retailers' terms before republishing their data (not yet reviewed).
- The branch keeps **one commit** (the database), so it doesn't grow in history; your clone of `main` stays small.
- Scheduled workflows on a public repo can be paused by GitHub after about 60 days without repo activity; if the
  daily run stops appearing, re-enable it in the Actions tab.
- The cloud and the Mac never write the database at the same time: only the cloud job holds it, and the Mac only
  adds new inbox files. If both touch the branch at once, whichever finishes second retries.
- GitHub's runner IPs can be blocked later; the scraper stops on a block and backs off, it never tries to get past one.
