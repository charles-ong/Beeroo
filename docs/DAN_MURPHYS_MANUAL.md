# Adding Dan Murphy's prices by hand

Dan Murphy's serves bot protection to our automated scraper, and we don't try to get past it. You can still
add its prices yourself: browse the page in your normal browser, save what you see, and import it. No automated
visit to their site is involved. A few minutes per state, probably weekly.

## Each time (per state)

1. **Set the store.** On danmurphys.com.au choose pick-up/delivery and enter the state's pricing postcode:
   NSW 2100, ACT 2600, VIC 3000, QLD 4000, SA 5000, WA 6000, TAS 7000, NT 0800
   (the same ones the rest of the site uses). Pick the nearest store it offers.
2. **Open** <https://www.danmurphys.com.au/beer/all> and click **Load more** until the button disappears
   (several hundred products). Scroll to the bottom to check.
3. **Save it**, either way:
   - **Console snippet (most reliable).** Open the browser's developer console (Chrome: View → Developer →
     JavaScript Console), paste this and press Enter. It copies the product cards to your clipboard; paste them into a
     new file such as `dm-nsw.json`:
     ```js
     copy(JSON.stringify({cards:[...document.querySelectorAll('shop-product-card')].map(c=>{const a=c.querySelector('a[href*="/product/"]');return {href:a?a.getAttribute('href'):'',lines:c.innerText.split('\n')}})}))
     ```
   - **Save the page.** File → Save Page As → **Webpage, Complete** (not "HTML only"). If the importer says no
     product cards were found, use the console snippet instead.
4. **Import it**, naming the state you set in step 1:
   ```bash
   .venv/bin/python scripts/import_dm_page.py ~/Downloads/dm-nsw.json --state NSW
   ```
   Add `--dry-run` first to see what it found without sending anything.

## What it does

- Reads the cards exactly like the scraper's card parser (`scrapers/dan_murphys_dom.py`): name, case/pack/each
  prices, member offers (kept, flagged as member prices), skipping in-store-only prices. ABV comes only from the
  name; it is never invented.
- Refuses a page with fewer than 40 products (you probably didn't finish "Load more"; `--force` overrides), or one
  where many cards can't be read (the layout may have changed).
- Files the prices under the state you give, with that state's pricing postcode, and a store called
  "Dan Murphy's (imported by hand)" unless you pass `--store-id`/`--store-name`/`--suburb`.
- **Where it goes:** if `BEEROO_DATA_REMOTE` is set in `~/.config/beeroo/env`, to the cloud job's inbox (the next
  daily cloud run adds it to the database and the site, exactly like your nightly Dan Murphy's pages). Otherwise,
  or with `--local`, straight into `data/beeroo.sqlite3`. If the cloud can't be reached, the page waits in
  `data/outbox/` and the next nightly run pushes it.
- Safe to repeat: importing the same page twice adds nothing the second time.
- The observation time is when the file was saved (override with `--observed-at`).

## Limits

- Prices are only as fresh as your last import. The site shows each state's Dan Murphy's date, so you can see how old it is.
- It's your own browsing, but Dan Murphy's terms of use may still restrict copying or republishing price data. I
  haven't reviewed them; check before publishing the site publicly.
