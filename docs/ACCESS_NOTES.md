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

## BWS / Liquorland status
- No fixtures yet. `scrapers/endeavour.py` is a shared, retailer-parameterised parser for the Dan Murphy's "Browse" format; BWS (also Endeavour) *may* use the same format - **unverified**.
- Liquorland (Coles Group) has an unknown format (robots.txt hints at Fredhopper `fh_` parameters). No parser written until we have real data.
- Plan: capture via `docs/CAPTURE_GUIDE.md` + `scripts/har_to_fixtures.py`.

## BWS and Liquorland findings (from manual captures, 2026-10-01)

Captured in a normal browser, logged out, locations 2606 (ACT) and 6000 (WA). Raw captures and HARs are gitignored (they contain cookies/tokens; one capture held a live anonymous bearer token, deleted). Curated, trimmed fixtures live in `tests/fixtures/`.

**Prices differ by location at both retailers** (the core premise of postcode support is confirmed):
- BWS: 107 of 207 products common to Woden (ACT, store 6723) and Perth (WA, store 4261) had different prices; the catalogue/availability also differs.
- Liquorland: 26 of 59 common products differed between state sites `ll_act` and `ll_wa`.

**BWS** (`scrapers/bws.py`): `GET /apis/ui/ProductGroup/Products/<group>` -> `Items[].Products[]`, one product per pack size with a shelf `Price`. ABV is in `AdditionalDetails["alcohol%"]` (100% coverage). Different (older) format from Dan Murphy's, so it does not use `scrapers/endeavour.py`. Location: `Address/SetPickupByStoreNo` -> `FulfilmentInfo.ClickAndCollectDetails`. "on app for" prices are app-only (flagged member_only); "N for $X" is a public multi-buy. Unknown whether pricing is per store or per state; keyed per store for now. **Caution:** product responses that load *before* a store is chosen are for an IP-geolocated default store (Umina NSW in this capture), so only trust responses after the store-selection call.

**Liquorland** (`scrapers/liquorland.py`): `/api/products/<site>/beer-and-cider` (60/page, ~816-890 products incl. cider), one entry per pack variant (`<sku>_ea`, `PACK6`, `CTN24`...). Pricing is **per state** (`ll_act`, `ll_wa`), keyed `liquorland:ll_<state>`. The list has **no ABV** (only ~6% of names state it); ABV needs the per-product detail endpoint (`alcoholPercent`).

### Blocking issue: Liquorland robots.txt
Liquorland's `robots.txt` has `Disallow: /api/*` and its category HTML is client-rendered (no product data), so the data can only be fetched from disallowed URLs, and ABV would need one extra disallowed request per product. Combined with the ShieldSquare CAPTCHA, **an automated Liquorland scraper would break our own ground rules.** The parsers are written for manually captured data only. Options: manual/periodic captures, ask Coles Group for a feed, or exclude Liquorland ABV/standard-drink from the MVP (compute from names where present).

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
