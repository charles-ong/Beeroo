# Retailer access notes (checked 2026-10-01)

| Retailer | robots.txt | Automated access |
|---|---|---|
| Dan Murphy's | Unreadable: plain HTTP request got a Cloudflare block page | Blocked for plain HTTP clients. Earlier Playwright run (2026-09-19) succeeded. |
| BWS | Same Cloudflare block | Blocked for plain HTTP clients. Untested otherwise. |
| Liquorland | Readable. Disallows `/api/*`, `/search`, `/user`, multi-filter category URLs. Plain category pages not disallowed. | ShieldSquare CAPTCHA shown to an in-app browser on a terms page. |

Terms of Use for all three are **not yet reviewed** (Liquorland's is at `/termsconditions`, behind the CAPTCHA). Review manually in a normal browser.

Ground rules: no CAPTCHA solving, no stealth/proxy/fingerprint evasion, low request rates, honour robots.txt, attribute and link every price to its source page. If blocked, stop and report.

## Dan Murphy's findings (2026-10-01)

- **Headless Chromium: HTTP 403 (Cloudflare) on first request.** Headed Chromium worked initially.
- **After ~9 page loads in ~45 minutes from one IP (probing + test runs), the headed browser was also served the Cloudflare block page.** Stopped; no evasion attempted. Expect to need long gaps between runs (hours, not minutes), or an approved data feed.
- The category page itself calls `POST /apis/ui/Browse` (24 products/page, `TotalRecordCount` ~404 for beer). The response carries brand, size, **ABV (`webalcoholpercentage`)**, stock code, and per-pack prices, so no detail-page requests are needed. The scraper only *reads* these responses from a normal page load; it never calls the API itself.
- Location is store-based. Header "Pick up: <store>" opens a cart drawer -> "Change" -> postcode/suburb box (calls `StoreLocator/Suburbs`, then `StoreLocator/Stores/danmurphys`) -> choose a store. The site defaults to a store from IP geolocation.
- Price semantics: `caseprice`/`singleprice` = standard price; `promoprice` = member/multi-buy offer; "in-store" `each` prices are not purchasable online and are skipped.

### Not yet verified live
- Clicking a store card actually changes the store and Browse prices (final steps of `select_location`).
- Full pagination to ~404 products via "Load more".
- Whether prices differ between stores/states (the key assumption behind postcode support).

## BWS and Liquorland findings (from manual browser captures, 2026-10-01; superseded by the live scrapers below)

Captured earlier in a normal browser, logged out, for locations 2606 (ACT) and 6000 (WA). Curated, trimmed copies are the test fixtures in `tests/fixtures/`. (Raw HAR files contain cookies/tokens and must not be committed or shared; `*.har` is gitignored.)

**Prices differ by location at both retailers** (the core premise of postcode support is confirmed):
- BWS: 107 of 207 products common to Woden (ACT, store 6723) and Perth (WA, store 4261) had different prices; the catalogue/availability also differs.
- Liquorland: 26 of 59 common products differed between state sites `ll_act` and `ll_wa`.

**BWS** (`scrapers/bws.py`): `GET /apis/ui/ProductGroup/Products/<group>` -> `Items[].Products[]`, one product per pack size with a shelf `Price`. ABV is in `AdditionalDetails["alcohol%"]` (100% coverage). Different (older) format from Dan Murphy's, so it does not use `scrapers/endeavour.py`. Location: `Address/SetPickupByStoreNo` -> `FulfilmentInfo.ClickAndCollectDetails`. "on app for" prices are app-only (flagged member_only); "N for $X" is a public multi-buy. Unknown whether pricing is per store or per state; keyed per store for now. **Caution:** product responses that load *before* a store is chosen are for an IP-geolocated default store (Umina NSW in this capture), so only trust responses after the store-selection call.

**Liquorland** (`scrapers/liquorland.py`): `/api/products/<site>/beer-and-cider` (60/page, ~816-890 products incl. cider), one entry per pack variant (`<sku>_ea`, `PACK6`, `CTN24`...). Pricing is **per state** (`ll_act`, `ll_wa`), keyed `liquorland:ll_<state>`. The list has **no ABV** (only ~6% of names state it); ABV needs the per-product detail endpoint (`alcoholPercent`).

### Liquorland robots.txt
Liquorland's `robots.txt` has `Disallow: /api/*` and its category HTML is client-rendered (no product data), so the data can only come from those disallowed URLs. Its ABV is only available per product from yet another disallowed request. The scheduled scraper includes Liquorland **at the operator's request**, never solves a CAPTCHA, and can be switched off (`BEEROO_SKIP_RETAILERS=liquorland`).

## BWS live findings (2026-10-01, from a visible-browser run, Canberra 2600)

- `robots.txt` (fetched in-browser, HTTP 200) disallows only `/search`, `/my-profile`, `/my-account/order-history`, `/my-account/my-profile`, `/checkout`, `/customer-reviews`, `/Akamai`. Category pages are allowed. Not blocked by bot protection.
- Full beer list: `https://bws.com.au/beer/all-beer` (745 beers; 40 per page via a **"LOAD MORE" link**, not a button). Each load calls `GET /apis/ui/Browse?...&pageNumber=N&pageSize=40`, whose response carries `Bundles` (same shape as ProductGroup `Items`). Store: modal "Set Your Store" -> "Enter postcode or suburb" -> suggestion -> **SELECT** on a store -> `POST /apis/ui/Address/SetPickupByStoreNo`.
- A store stocks only ~200-300 of the 745 (`IsAvailable`/`StockOnHand` per store); the rest are skipped as out of stock.
- **Data quirks found only with real data (all fixed + tested in `tests/test_bws_live.py`):**
  - `liquorsize` is unreliable: across an item's products it can hold the *largest pack's* volume (6000ML on a single can, a 4-pack and a 16-case alike). Per-unit volume comes from the product **name**.
  - Some products are sold as "N Pack" with `productunitquantity` 1: the name states N.
  - Names like "Cans 10x375ml": the unit is a 10-pack, so units = 10 x quantity (quantity 3 "Case" = 30 cans).
  - A `webpacktype: Case` with quantity 1 and no count: count = `liquorsize` / per-can volume only if a clean whole number, else skipped.
  - Multi-buy tags ("2 for $120") are sometimes stamped onto the single can as well as the carton; kept only if cheaper than buying separately.
- Result on the real capture: 204 in-stock products, ABV on 203, $/standard drink range $1.10-$10.97 for beers over 2% ABV.


## Liquorland live findings (2026-10-01, visible-browser run, NSW 2100)

- Loaded normally (HTTP 200); **no CAPTCHA** appeared for a visible Playwright Chromium. (An earlier plain terms-page load from another browser did get a ShieldSquare CAPTCHA, so this can change; the scraper stops if it ever sees one.)
- `https://www.liquorland.com.au/beer-and-cider/beer` -> `GET /api/products/ll/<state>/beer-and-cider?page=N&show=60&v=2&facets=beer` (note `ll/<state>`, e.g. `ll/nsw`; the payload's own `debugQuery` says `sitestate=ll_nsw`). 987 entries for NSW (816 for ACT): each pack variant (`<sku>_ea`, `PACK6`, `CTN24`) is its own entry; merged by SKU that is ~392 products.
- Location: a modal "See what's available near you" (`input[aria-label^="Enter a suburb or postcode"]`) -> suggestion button `search-results-item` -> "Save location" -> a "Set shopping method" drawer listing stores nearest first (`div.StoreItem`) -> "Save & continue shopping". Only then does the site switch to the state's catalogue.
- Pagination: a pager with 20/40/60/80 results per page and a real `Go to next page (N)` link. (The "Show more" button on the page is a sidebar filter expander, not pagination.)
- Prices are per **state**, not store; `unit prices` $1.25-$13.50 on the real NSW capture, volumes consistent. The list has **no ABV** (3 of 392 products stated it in the name).

## Dan Murphy's live findings

Headless Chromium is blocked (HTTP 403, Cloudflare). A visible browser worked at first (see above for the Browse API and store picker), then got blocked after ~9 page loads from one IP, and was still blocked hours later. It remains blocked from the development machine.

## Product matching across retailers

Names differ between retailers ("Sapporo Premium Lager Bottles 355ml" vs
"Sapporo Bottles 355mL"). `common/matching.py` merges automatically only when
the volume matches, packaging and ABV don't conflict, and the names are
identical after removing generic words and pack counts, or differ only by a
harmless descriptor (premium, original, cerveza, alcoholic, classic, best) with
exactly one candidate on each side. Everything else is left unmerged and listed
by `python scripts/match_report.py`. Fix pairs permanently in
`data/match_overrides.csv` (`merge` / `never_merge` rows, SKUs from the report).

## Dan Murphy's: two ways to read the same page

`scrapers/dan_murphys.py` loads the category page in a visible browser and reads
it one of two ways (`BEEROO_DM_METHOD`, default `auto`):

- **json**: the page's own `POST /apis/ui/Browse` responses (richest: ABV, pack sizes). Ingest kind `dan_murphys_browse`.
- **cards**: the text of each `shop-product-card` ("$71.99 case (24)", "MEMBER OFFER ... Non-Member: ..."),
  the approach that worked unblocked from a home connection. Ingest kind `dan_murphys_cards`, parsed by
  `scrapers/dan_murphys_dom.py`. Used automatically when no Browse response is seen.

Both go through the same store selection (nearest store to the state's postcode) and the same schema; the card
parser keeps member and non-member prices (flagged), skips in-store-only prices and never guesses a unit count
or ABV. Neither method helps if the site serves a bot-protection page: that is detected and the scraper backs off.

## Reviews

Only the **average rating and review count** are collected, from data the list pages already load (no extra
requests per product):

- **BWS**: `OverallRating` / `NumberOfReviews` on each product (the pack variants report slightly different counts; the largest is used).
- **Dan Murphy's**: the same two fields in the Browse JSON; from the card text path, the star icons (full stars plus the partial star's width) and the "(116 REVIEWS)" text.
- **Liquorland**: its list data has no ratings. The per-product detail endpoint does (`ratings.average` / `total`), but fetching it means one request per product per state, so it isn't done.

Individual review text isn't collected: it would need a page or API request for every product. The site pools the
retailers' averages weighted by review count (`queries.combined_rating`) and shows each retailer's own figure in the modal.
