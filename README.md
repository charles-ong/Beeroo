# Beeroo

Compare beer prices, price per standard drink, and price history across Dan Murphy's, BWS and Liquorland by postcode.

**Status:** early prototype. Only a Dan Murphy's beer scraper exists. See `MVP_PROMPT.md` for the MVP plan and `docs/ACCESS_NOTES.md` for retailer access/legal findings.

## Setup

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
playwright install chromium
python main.py --postcode 3000   # scrapes Dan Murphy's beer into data/beeroo.sqlite3
```

Run tests: `pip install -r requirements-dev.txt && pytest`. Parser fixtures live in `tests/fixtures/`.

Notes: headless Chromium is blocked by Cloudflare, so the default is headed (`--headless` tries headless first and falls back). Space runs far apart; repeated runs get blocked. Set `BEEROO_CHROMIUM_PATH` to use a specific Chromium build.

## Running the web app

```bash
python scripts/build_demo_db.py --raw     # demo DB: real current prices + SIMULATED history (flagged in the UI)
BEEROO_DB=data/demo.sqlite3 uvicorn app.main:app --reload
# open http://127.0.0.1:8000  (API docs at /api/docs)
```

Without `BEEROO_DB` it serves `data/beeroo.sqlite3` (real data only, history starts when you first collect). API: `GET /api/compare?postcode=2606&sort=value&pack=case&q=...`, `GET /api/products/{id}?postcode=...`, `GET /api/locations?postcode=...`. A postcode maps to a state; each retailer's prices come from a same-state location if loaded, otherwise a fallback that the UI flags.

## Ingesting captured data (BWS, Liquorland) and matching

Liquorland/BWS data comes from manual browser captures (see `docs/CAPTURE_GUIDE.md`), loaded with:

```bash
python scripts/ingest.py bws --products a.json b.json --set-pickup pickup.json
python scripts/ingest.py liquorland --products page1.json page2.json
python scripts/ingest.py match     # re-run cross-retailer matching
```

Liquorland publishes no ABV in its list data and its `/api/*` is disallowed by robots.txt, so standard-drink values for Liquorland products appear only when the name states ABV or the same product is matched at a retailer that publishes it (`products.abv_source` records which). Otherwise status is `abv_unknown` and no figure is shown.

## Standard drinks

`standard_drinks = volume_litres x ABV% x 0.789` (Australia: 10 g alcohol per standard drink).
