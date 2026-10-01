# Liquorland: refreshed by hand

**Why not automated:** Liquorland's `robots.txt` disallows `/api/*` (the only place its price data comes from), its product pages render in the browser with no data in the HTML, and it shows a CAPTCHA to automation. We don't build around that. Instead you browse its beer pages yourself, normally, and the extension records what your browser receives.

Liquorland prices are **per state**, so only four captures cover our four cities: NSW, ACT, VIC, WA.

## The routine (about 5 minutes, e.g. weekly)

Prerequisites: the extension installed and turned on (`extension/README.md`), you're **logged out** of Liquorland, and your local server running in single-user mode:

```bash
BEEROO_QUORUM=1 .venv/bin/uvicorn app.main:app --no-access-log
```

For each of these postcodes (Sydney 2000, Canberra 2600, Melbourne 3000, Perth 6000):

1. Open https://www.liquorland.com.au/beer-and-cider/beer in Chrome.
2. Set your location to that postcode (the "See what's available near you" box at the bottom of the page) and save it.
3. Click **Load more** until the list stops growing (about 14 pages).
4. Open the extension popup: Liquorland should say "Sent and accepted". If it says it couldn't confirm you're logged out, see the extension's Diagnostics; the per-site switch is there for that case.

Then check what's still due:

```bash
.venv/bin/python scripts/check_freshness.py
```

## ABV

Liquorland's list data has no ABV. Standard-drink values for Liquorland products appear only when the name states the ABV or the same product is matched at BWS/Dan Murphy's (labelled "ABV from BWS"). Otherwise the page says "ABV unknown". See `docs/ACCESS_NOTES.md`.

## If you'd rather not do this

Ask Coles Group (Liquorland's owner) about a price feed, or leave Liquorland out; the other two retailers work without it.
