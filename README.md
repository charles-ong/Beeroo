# Beeroo

Compare beer prices, **price per standard drink**, and price history across **Dan Murphy's, BWS and Liquorland**.

You pick your **state or territory** from a dropdown. Every price is for the store nearest that state's pricing postcode (for example **2100** for NSW; the others use their capital city: see `common/states.py`). No postcode entry.

## How it works (free, no server)

```
your computer, once a day:
  scrape BWS + Liquorland + Dan Murphy's for every state/territory (visible browser)
  -> local SQLite database -> static website -> free static host (GitHub/Cloudflare Pages)
```

- `scripts/scheduled_scrape.py`: the daily scrape. [docs/SCHEDULED_SCRAPE.md](docs/SCHEDULED_SCRAPE.md)
- `scripts/export_static.py` + `scripts/publish_pages.sh`: build and publish the site. [docs/FREE_HOSTING.md](docs/FREE_HOSTING.md)
- Changing wording or looks: edit `app/static/` and push; the site redeploys itself ([docs/EDITING_THE_SITE.md](docs/EDITING_THE_SITE.md)). Never edit the generated `site/` folder.
- `scripts/import_dm_page.py`: add Dan Murphy's prices from a page you browsed and saved yourself (no automated access). [docs/DAN_MURPHYS_MANUAL.md](docs/DAN_MURPHYS_MANUAL.md)
- The site's filters: retailer chips (pick several to see only beers every picked retailer sells; none picked = any), type of beer (a checkbox dropdown; best-effort from the product name: `common/beer_types.py`; "Zero" on its own means non-alcoholic), quantity range, ABV range, member offers (on by default), search and sort (best value, lowest price per can/bottle, highest rated, highest ABV, name). Titles hide pack size and volume (shown separately). Cards and the product modal show the average star rating (pooled across retailers by review count) where the retailer publishes one.
- `scripts/daily_run.sh`: the whole chain (what the scheduler runs). Publishes with `scripts/deploy_cloudflare.sh` (Cloudflare Pages) and/or `scripts/publish_pages.sh` (GitHub Pages).
- `.github/workflows/daily-scrape.yml`: the daily cloud job (BWS + Liquorland on a free GitHub runner, then deploy); your Mac scrapes Dan Murphy's only. [docs/CLOUD_SCRAPE.md](docs/CLOUD_SCRAPE.md)
- `.github/workflows/cloud-scrape-experiment.yml`: a one-click test of whether a free GitHub runner can scrape a site. [docs/SCHEDULED_SCRAPE.md](docs/SCHEDULED_SCRAPE.md#can-the-scraping-move-to-the-cloud-experiment)
- `scripts/check_freshness.py`: which state/retailer combinations are stale.
- `app/`: the API + frontend (also used locally, or for the optional paid server in [docs/DEPLOYMENT.md](docs/DEPLOYMENT.md)).
- [docs/ACCESS_NOTES.md](docs/ACCESS_NOTES.md): what each retailer's site does, what's been verified live, and the data quirks found.

**Read before relying on it:** the retailers' terms of use are unreviewed, Liquorland's `robots.txt` disallows the API its pages load (it's included at your request and can be switched off), and Dan Murphy's is blocked on the machine it was developed on. Details in the docs above.

## Setup

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt -r requirements-dev.txt
playwright install chromium      # or set BEEROO_CHROMIUM_PATH to an existing Chromium
pytest -q                        # 270+ tests
```

Try one state by hand first (a browser window opens and you can watch it):

```bash
python scripts/scheduled_scrape.py --zone NSW --retailer bws
python scripts/export_static.py && python3 -m http.server -d site 8000   # then open http://127.0.0.1:8000
```

Local server instead of static files: `BEEROO_DB=data/beeroo.sqlite3 uvicorn app.main:app --no-access-log`. API: `GET /api/states`, `/api/compare?state=NSW&sort=value&pack=case&q=...`, `/api/products/{id}?state=NSW`.

## Standard drinks

`standard_drinks = volume_litres x ABV% x 0.789` (Australia: 10 g alcohol per standard drink). Liquorland's list data has no ABV, so a value figure appears for its products only if the name states the ABV or the same product is matched at a retailer that publishes it (labelled "ABV from BWS"); otherwise it shows "ABV unknown". It's a value comparison, not encouragement to drink more.
