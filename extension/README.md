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
cd extension && node --test "test/*.test.mjs"     # 46 tests, no dependencies
cd .. && pytest tests/test_extension_payloads.py   # server accepts what the extension builds
```

These cover URL matching (incl. look-alike hosts), the allowlist, logged-out detection, the send/skip pipeline (consent, pause, dedupe, rate limit, store waiting, delivery mode), the page interceptor in an isolated VM (it must never change what the page sees), and the manifest's permissions.

## NOT verified yet: needs a real browser (please test these)

The unit tests use captured data. These parts were **never run in Chrome against the live sites**:

- [ ] Extension loads without errors on `chrome://extensions` (check the service worker "Inspect views").
- [ ] **Logged-out detection:** on each site, while logged out, header/nav text contains a Login/Sign-in control that our check recognises. If not, the extension will silently send nothing; open the retailer page, DevTools console, and check what `content.js` collects (add a `console.log` in `collectTexts`).
- [ ] **Endpoint paths:** BWS product lists are assumed to be `/apis/ui/ProductGroup/Products/<group>` with group starting `beer`, and the store endpoints `Address/SetPickupByStoreNo` and `StoreLocator/Store` (inferred from capture file names, not seen as raw URLs). Liquorland assumed `/api/products/ll_<state>/<category>`.
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
