# Beeroo

Compare beer prices, price per standard drink, and price history across Dan Murphy's, BWS and Liquorland by postcode.

**Status:** early prototype. Only a Dan Murphy's beer scraper exists. See `MVP_PROMPT.md` for the MVP plan and `docs/ACCESS_NOTES.md` for retailer access/legal findings.

## Setup

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
playwright install chromium
python main.py   # writes data/dan_murphys_beer.json
```

Run tests: `pip install -r requirements-dev.txt && pytest`. Parser fixtures live in `tests/fixtures/`.

## Standard drinks

`standard_drinks = volume_litres x ABV% x 0.789` (Australia: 10 g alcohol per standard drink).
