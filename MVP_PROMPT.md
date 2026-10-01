# Prompt: Rework Beeroo into an MVP price-comparison web app

Copy everything below the line into a new Claude Code session opened in this repo.

---

## Goal

Beeroo currently contains a single-retailer scraper prototype. Rework it into an MVP web application where a user enters their **Australian postcode** and can compare, across **Dan Murphy's, BWS and Liquorland**:

1. **Price** of the same product at each retailer (for stores serving that postcode)
2. **Alcohol value**: price per standard drink (and cost per 100 mL pure alcohol as a secondary metric)
3. **Price history** over time per product per retailer

Start by reading the existing code, then write a short plan (architecture, data model, milestones) and get my sign-off before building. Ask me about any decision that is genuinely mine (hosting, budget, legal appetite) and otherwise pick sensible defaults and tell me what you picked.

## What exists today (read before changing)

- `main.py`: launches Playwright Chromium (`HEADLESS = False`), calls `scrape_all`, dedupes, writes `data/dan_murphys_beer.json`.
- `scrapers/dan_murphys.py`: scrapes only `https://www.danmurphys.com.au/beer/all`. Clicks "Load more" up to 50 times with fixed sleeps, then reads each `shop-product-card` via `inner_text()` (or BeautifulSoup, switched by a `SCRAPE_METHOD` constant). Also scrapes a "current offers" carousel.
- `common/parsing.py`: regex parsing of card text lines into `{price, quantity, unit}`; handles "member offer / non-member", "$X for N packs" multi-buys.
- `common/output.py`: timestamp helper and `deduplicate_products` (key = name, quantity, unit).
- `data/dan_murphys_beer.json`: 353 records. Fields: `name, price, quantity, unit, url, source, scraped_at`.
- `danmurphys.html`: 1.3 MB saved page snapshot (debugging artefact, not source).
- `.venv/` is checked into the project folder (Python 3.14; `__pycache__` shows 3.11 was also used). `requirements.txt` lists only `beautifulsoup4` and `playwright`, though pandas/numpy are installed in the venv.
- Not a git repo, no tests, no README, no config.

## Known issues and limitations to fix

**Data completeness**
- Dan Murphy's **beer only**. No BWS, no Liquorland, no other categories (decide MVP scope: beer first, then cider/RTD/spirits/wine if time allows).
- **No ABV and no volume as structured fields.** Volume is only embedded in the name string (e.g. "355mL"; 337/353 names), ABV appears in only ~33/353 names. Standard drinks cannot be computed from current data. Needs ABV from the product detail page / structured data (JSON-LD `Product` blocks exist in the saved HTML) or a reliable fallback source.
- **No location.** Prices and availability at all three retailers vary by store/state, so a postcode can't currently influence anything.
- **No price history.** Each run overwrites one JSON file with no retained snapshots.
- No product ID, brand, image, category, pack size, or per-unit volume. The Dan Murphy's product ID is in the URL (`/product/250183/...`) and should be the retailer-side key.

**Data quality**
- `unit` values are inconsistent (`case`, `cases`, `pack`, `bottles`, `each`, `block`) and `quantity` semantics vary by unit. Normalise to `units_per_pack` + `unit_volume_ml` + `total_volume_ml`.
- Dedup key ignores price, so different price tiers (single / 4-pack / case; member / non-member) can collapse or double-count depending on order. Model **price options** (single, pack, case, multi-buy) as separate rows under one product, with a flag for member-only price vs standard price. Show the non-member price by default.
- Regex text parsing of `inner_text()` is brittle; any UI copy change silently breaks it. Prefer JSON-LD, embedded page state, or the retailer's own JSON API calls observed in the network tab, over rendering and parsing display text. Validate every record (price > 0, volume plausible, ABV 0–100) and fail loudly on schema drift.
- Cross-retailer **product matching** doesn't exist. Plan for it: normalise brand + product name + volume + pack size, with GTIN/barcode where available, fuzzy match fallback, and a manual override table. Surface match confidence; never silently merge uncertain matches.

**Scraper robustness**
- Fixed `wait_for_timeout` sleeps, no retries/backoff, broad `except Exception: print`, debug files written to the working dir, `HEADLESS = False`, hardcoded constants instead of config, no logging, no rate limiting, no tests.
- Needs: headless by default, retries with exponential backoff, polite rate limiting, structured logging, per-run health report (counts, failures, % with ABV/volume), and fixture-based parser tests using saved HTML/JSON.

**Repo hygiene**
- Add git + `.gitignore` (`.venv/`, `__pycache__/`, `data/*.json`, debug HTML/PNG). Remove/relocate `danmurphys.html` into `tests/fixtures/`. Pin dependencies (`pyproject.toml` or locked requirements), pick one Python version. Add README with setup/run instructions.

## MVP requirements

**User flow**
1. Landing page: postcode input (validate 4-digit AU postcode; map to state and nearby stores; remember in localStorage).
2. Results list for that postcode: product, pack/size, ABV, **price at each retailer side by side**, **$ per standard drink**, cheapest highlighted, last-updated time per price. Sort by price per standard drink (default), unit price, total price. Filter by category, retailer, pack size, ABV range, search by name.
3. Product detail page: all retailer prices, price history chart (line per retailer, selectable range 1M/3M/1Y/all), min/max/current, "is this a good price?" indicator vs history, links out to each retailer's product page.
4. Clear empty/partial states: product not stocked at one retailer, postcode not covered, stale data, price missing.

**Standard drink formula** (Australia, 10 g alcohol): `standard_drinks = volume_litres × ABV% × 0.789`. Price per standard drink = pack price ÷ (standard drinks per unit × units). Show to 2 decimal places, include a short "how this is calculated" note and an alcohol-responsibility footer (e.g. drinkwise.org.au). No promotional language encouraging heavy drinking; the metric is for value comparison.

**Postcode handling**
- Investigate how each retailer sets store/delivery location (cookies, API params, store-selector endpoints). Resolve postcode → store(s)/state, scrape per location group rather than per postcode, and store the `location_key` with each price. Document the approach and its limits. For MVP it's acceptable to cover a handful of representative state/metro locations and fall back to the nearest covered one with a visible notice.

**Architecture (suggested; propose alternatives if you disagree)**
- Scraper workers (Python + Playwright, or plain HTTP when a JSON endpoint is available), one module per retailer behind a common `Retailer` interface returning validated, typed records (pydantic).
- Postgres (or SQLite for local dev) with tables roughly: `products` (canonical), `retailer_listings` (retailer, retailer_sku, url, product_id nullable, match_confidence), `price_observations` (listing_id, location_key, price, unit_price fields, member_only, observed_at; **append-only**), `stores/locations`. Only write a new observation when price changes or once per day to bound storage.
- Backend API (FastAPI) + frontend (Next.js or Vite/React + a chart lib). Server-side computed fields for standard drinks and price per standard drink.
- Scheduled scrapes (cron / GitHub Actions / hosted scheduler), idempotent and resumable.
- Deploy target: a cheap setup (e.g. Fly/Render/Vercel + managed Postgres); tell me the monthly cost.

**Out of scope for MVP**: user accounts, price alerts, mobile app, in-store stock levels, checkout/affiliate flows.

## Legal and ethical guardrails (do this first)

- Check each retailer's `robots.txt` and Terms of Use regarding automated access and re-publishing price data, and summarise the risk to me in plain language before building scrapers. Do not bypass bot protection, CAPTCHAs, or logins. If a retailer blocks automated access, stop and report rather than working around it, and propose alternatives (official/partner feeds, manual data, a lower scrape frequency, or showing links instead of cached prices).
- Keep request rates low, identify the scraper honestly in the User-Agent where practical, cache aggressively, and attribute the retailer on every price with a link to the source page.
- Note: Dan Murphy's and BWS are both Endeavour Group brands (shared infrastructure may help), Liquorland is Coles Group.

## Delivery plan

Work in small, reviewable milestones, committing after each:

1. Repo hygiene + plan document + legal/robots summary.
2. Common data model + schema + migrations; parsers moved to validated typed records; tests from fixtures.
3. Dan Murphy's scraper refactored (structured data, ABV/volume from detail pages, postcode/location support) writing to the DB with history.
4. BWS scraper, then Liquorland scraper, same interface.
5. Product matching across retailers + review tooling for low-confidence matches.
6. API + frontend: postcode entry, comparison table, product detail with history chart.
7. Scheduling, health monitoring, deployment, README.

## Definition of done for the MVP

- Entering a valid postcode shows beer products with prices from all three retailers where stocked, with $/standard drink and last-updated times.
- At least 2 weeks' worth (or seeded/backfilled) price observations render in a history chart on the product page.
- ≥ 90% of listed products have ABV and volume populated; the rest are clearly flagged and excluded from the standard-drink sort.
- Scrapers run unattended on a schedule, with a health report; parser tests pass in CI.
- README explains setup, architecture, how to add a retailer, and the legal caveats.

Before writing code, show me your plan and the top 3 risks (I expect: postcode-specific pricing access, ABV data availability, and anti-bot/legal constraints).
