# Capturing retailer data from your own browser

Liquorland and BWS block automated access, so parsers are built from pages **you** browse normally. No automated requests are made to those sites.

## Steps (Chrome / Edge / Safari-equivalent)

1. Open DevTools (Cmd+Opt+I) -> **Network** tab. Tick **Preserve log**.
2. Browse like a normal shopper, slowly:
   - Open the **beer** category page.
   - Set your location: enter postcode **3000**, choose a store, then repeat with **2000** and **6000** (different states) - on the same product list. This tells us whether prices vary by location.
   - Click **Load more** / scroll until a few pages have loaded.
   - Open 2-3 individual product pages.
3. Right-click in the Network list -> **Save all as HAR with content**. One HAR per retailer is fine.
4. **Do not share the raw HAR** - it contains cookies and tokens. Run the extractor locally; it copies only response bodies:

```bash
python scripts/har_to_fixtures.py liquorland.har --list
python scripts/har_to_fixtures.py liquorland.har --match api --html --out tests/fixtures/liquorland
```

5. Check the files in `tests/fixtures/<retailer>/` contain no personal data (logged-in name, address) before committing. Browse logged out to avoid this.

Then tell Claude the folder names and what postcode/store each capture used.

## Please also note
- Which prices you see with and without being logged in / a member.
- Any CAPTCHA or block you hit (do not solve one on Claude's behalf).
