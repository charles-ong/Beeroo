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
