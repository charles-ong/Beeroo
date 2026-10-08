# Daily scraping in the cloud (BWS, Dan Murphy's and Liquorland)

```
 GitHub Actions (free runners)
   plan ─► scrape (bws, 8 states) ──────────────┐
           scrape (dan_murphys, 7 states) ──────┤ pages + back-off state
           scrape (liquorland, 1 state) ────────┤
                                                ▼
   publish: restore DB from `data` branch
            ingest this run's pages (+ any pages left in the branch inbox by a hand import)
            save DB to `data` branch ─► export site ─► deploy to Cloudflare Pages
```

Each retailer is its own job. They never touch the database; they hand their pages to `publish`, the only
job that writes the `data` branch. So a failed or blocked retailer can be re-run on its own.

**Schedule (UTC)** in `.github/workflows/daily-scrape.yml`: 19:00 = BWS (all 8 states) + Dan Murphy's (the 7 states
with stores: it has none in the NT) + 1 Liquorland state; 01:00, 07:00 and 13:00 = 1 Liquorland state each.

- **Dan Murphy's** is visited once per state, about 10 minutes apart. From a GitHub runner it let NSW and then ACT
  through once "Load more" was handled properly; it blocked cloud servers earlier, so it may again. A block backs it off
  2, 4, 8... days and never retries the same day. **Its prices and range differ by state** (about 1 product in 9 differs between NSW and ACT), so every
  state is scraped, never copied from another. When it is backing off, use the hand import
  (`docs/DAN_MURPHYS_MANUAL.md`), and make sure the site really shows that state's store first.
- **Liquorland**'s bot protection only tolerates a couple of visits a day from GitHub's addresses, so it gets one slow
  session per run, oldest state first: about 5 states a day, all 8 in under two days. If it still gets blocked, it backs
  off 2, 4, 8... days (we never try to get past it); the other option is to scrape it on your Mac too (see below).

Your Mac's nightly job (`scripts/install_launchd.sh`) is **not needed any more** and was uninstalled; Dan Murphy's
was blocked from it anyway. `scripts/daily_run.sh` still works if you ever want the Mac to scrape something
(`BEEROO_DATA_REMOTE` hands its pages to the cloud inbox).

## One-time setup

1. **Push the repo to GitHub** (`origin` is already `charles-ong/Beeroo`). The workflow lives in
   `.github/workflows/daily-scrape.yml`.
2. **Seed the cloud database with your existing price history**, from this folder:
   ```bash
   BEEROO_DATA_REMOTE=https://github.com/charles-ong/Beeroo.git .venv/bin/python scripts/data_branch.py seed --db data/beeroo.sqlite3
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
   BEEROO_DATA_REMOTE=https://github.com/charles-ong/Beeroo.git
   ```
   With that set, `scripts/daily_run.sh` scrapes only Dan Murphy's, pushes the pages and stops: it no longer
   scrapes BWS/Liquorland, exports or publishes. The Mac needs git access to the repo. The `https://` URL uses the same saved login as your
   normal `git push`; the `git@github.com:` form only works if you have an SSH key registered with GitHub. launchd needs no change.
6. **Try it**: Actions → Daily cloud scrape → Run workflow. Read the run summary (a table per state, then freshness).

## Re-running one retailer

On the run page, a failed leg shows as `scrape (liquorland)` (red). Click **Re-run failed jobs**: it re-runs
that leg and `publish` (BWS isn't scraped again, its pages from the first attempt are reused). Or start a new
run by hand: Actions → Daily cloud scrape → Run workflow, choose *retailers* (both / bws / liquorland), how many
*zones_per_run* (1-8; blank = BWS 8, Liquorland 1) and, if a site blocked us, *clear_backoff*.

## Day to day

- The run **fails (red, emails you)** when a site errored or blocked us, but only after it saved the data and deployed.
- `data/outbox/` on the Mac holds pages that couldn't be pushed (offline); they go with the next run.
- A page the cloud can't ingest is moved to `inbox/rejected/` on the `data` branch (also in the run's artifact).
- After a block, that retailer is skipped for 2, 4, 8... days. To retry sooner: Actions → Run workflow → *clear_backoff*.
- Back-off state is saved per retailer (`scrape_state_bws.json`, `scrape_state_liquorland.json`), so a block is respected across days.
- To drop Liquorland from the cloud job: remove its entry in the `plan` step of the workflow.
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
