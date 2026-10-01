# Daily scraping in the cloud (BWS + Liquorland) with your Mac doing Dan Murphy's

```
 GitHub Actions (free runners)                                    your Mac, daily 03:00 (launchd)
   plan ─► scrape (bws, 8 states) ───────┐                          scrape Dan Murphy's only (visible browser)
           scrape (liquorland, 1 state) ─┤ pages + back-off state   push pages to `data` branch inbox/
                                         ▼                                  │
   publish: restore DB from `data` branch ◄─────────────────────────────────┘
            ingest Dan Murphy's pages + this run's pages
            save DB to `data` branch ─► export site ─► deploy to Cloudflare Pages
```

Each retailer is its own job. They never touch the database; they hand their pages to `publish`, the only
job that writes the `data` branch. So a failed or blocked retailer can be re-run on its own.

**Schedule (UTC)** in `.github/workflows/daily-scrape.yml`: 19:00 = BWS (all 8 states) + 1 Liquorland state;
01:00, 07:00 and 13:00 = 1 Liquorland state each. Liquorland's bot protection only tolerates a couple of visits
a day from GitHub's addresses, so it gets one slow session per run, oldest state first: about 5 states a day,
all 8 in under two days. If it still gets blocked, it backs off 2, 4, 8... days (we never try to get past it);
the other option is to scrape Liquorland on your Mac too: put `BEEROO_LOCAL_RETAILER=all` and
`BEEROO_SKIP_RETAILERS=bws` in `~/.config/beeroo/env`, and remove Liquorland from the `plan` step of the workflow
(its pages then travel through the same inbox as Dan Murphy's).

Dan Murphy's blocks cloud servers (and headless browsers), so only your Mac scrapes it. Everything else
runs without your Mac. If your Mac is off, the site simply keeps yesterday's Dan Murphy's prices and the
freshness report says so. Cost: $0 (check GitHub's and Cloudflare's current free allowances).

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
