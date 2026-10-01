# Beeroo Price Contributor (prototype)

A Chrome (Manifest V3, Chrome 111+) extension. When you opt in, it reads the **beer** product lists your own browser receives on Dan Murphy's, BWS and Liquorland while you are **logged out**, filters them down to prices/sizes/ABV and the *store's* name/state/postcode, and sends them to a Beeroo server (`POST /api/contrib`, see `docs/CONTRIBUTION_API.md`).

It makes **no requests of its own**, never clicks or scrolls for you, and sends nothing until you tick the consent boxes on its settings page.

## Install (development)

1. Start the server: `BEEROO_DB=data/beeroo.sqlite3 uvicorn app.main:app --no-access-log`
2. Chrome → `chrome://extensions` → Developer mode → **Load unpacked** → choose this `extension/` folder.
3. The settings page opens. Read the live preview of what is sent, tick both boxes, keep the server at `http://127.0.0.1:8000`, **Save**.
4. Browse beer on a retailer site **logged out** (see "Manual test" below).

For a non-local server use `https://...`; Chrome will ask permission to contact it when you save.

## What it can and cannot do

| | |
|---|---|
| Reads | Only responses from specific product-list and store endpoints (`lib/matchers.js`), in the page's own JS world (`inject.js`). |
| Sends | An allowlisted copy (`lib/sanitize.js`): never cart, wish list, account, cookies, delivery address, descriptions or recommendations. |
| Stores locally | Install ID, settings, a log of what was sent (last 100), hashes for de-duplication. Nothing is sent anywhere else. |
| Logged in? | Sends only if it positively sees a "Log in/Sign in" control and no "Log out". If unsure it sends nothing. |

## Tests

```bash
cd extension && node --test "test/*.test.mjs"     # 59 tests, no dependencies
cd .. && pytest tests/test_extension_payloads.py   # server accepts what the extension builds
```

These cover URL matching (incl. look-alike hosts), the allowlist, logged-out detection, the send/skip pipeline (consent, pause, dedupe, rate limit, store waiting, delivery mode), the page interceptor in an isolated VM (it must never change what the page sees), and the manifest's permissions.

## What changed after real-world testing (2026-10-01)

It wasn't logging beers on the sites. Replaying real captured requests found three causes (all fixed, with regression tests built from the real shapes in `test/fixtures/real_requests.json`):

1. **Liquorland was never matched.** The real path is `/api/products/ll/<state>/<category>`; I'd guessed `ll_act` from a file name.
2. **BWS store selection was ignored.** `SetPickupByStoreNo` is a POST; only GET was accepted, so data would have been tagged with the default (IP-guessed) store.
3. **Dan Murphy's "Login" was never seen.** It lives in a `<span>` inside `shop-desktop-header`, not in `<header>`/`<nav>`. The collector now picks short text by *position* near the top of the page (verified against the real page markup in `tests/test_extension_dom.py`), and "Login My Dan's Account"-style text is accepted.

New: **Diagnostics** (popup and settings) explain, per site, why something was or wasn't sent, and show the header text seen when logged-out couldn't be confirmed. There's also a per-site "I'm logged out, skip the check" switch for sites whose header can't be read.

BWS and Liquorland header markup has **not** been seen; if either still shows "couldn't confirm you're logged out", send me the header text from Diagnostics.

## NOT verified yet: needs a real browser (please test these)

The unit tests use captured data. These parts were **never run in Chrome against the live sites**:

- [ ] Extension loads without errors on `chrome://extensions` (check the service worker "Inspect views").
- [ ] **Logged-out detection:** on each site, while logged out, header/nav text contains a Login/Sign-in control that our check recognises. If not, the extension will silently send nothing; open the retailer page, DevTools console, and check what `content.js` collects (add a `console.log` in `collectTexts`).
- [x] Endpoint paths and methods are now taken from real captured requests (BWS, Liquorland) and live probes (Dan Murphy's).
- [ ] **Store capture:** choose a pick-up store on each site and confirm the log shows the right store name/state. Choose *Delivery* and confirm nothing is sent.
- [ ] **Beer filter:** browse a wine page; nothing should be sent.
- [ ] **Network wrapping:** the retailer sites keep working normally (cart, search, pagination).
- [ ] **Server round-trip:** the popup/settings log shows "accepted"; `GET /api/contrib/health` counts increase; after a second install/browser contributes the same prices, they appear in the app.
- [ ] Pause/resume, reset ID, clear log.

## Known limitations

- A script running on the retailer's page could forge messages to the extension (`postMessage`). The server validates and needs a quorum, but this is a trust boundary to be aware of.
- The page's own scripts could in theory detect that `fetch`/XHR are wrapped.
- Retailer terms of use may restrict this; consent doesn't change that. Chrome Web Store policy could also affect distribution. Not reviewed.
- No icons yet (Chrome shows a default).
