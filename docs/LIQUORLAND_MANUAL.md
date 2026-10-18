# Adding Liquorland prices by hand

Liquorland's bot protection now rejects our automated visits (from GitHub's servers and from a home Mac), and we don't try to
get past it. You can still add its prices yourself: browse the site as a normal visitor, save what your browser loaded, and
import it. No automated visit to their site is involved. About 5 minutes per state, ideally weekly.

**Why a HAR file and not "Save Page As" HTML?** The page's HTML only holds the 80 products on screen, and the prices and pack
sizes come from data the page loads behind the scenes. A **HAR file** (a recording of those loads, which your browser saves
in one click) holds all pages of the list in a single file, in the exact form the importer already understands. Saving
"Webpage, Complete" would give you one file per page (13 per state) that the importer can't read.

Prices are **per state** (Liquorland has state-wide pricing; the store you pick doesn't matter), so do each state you want.

| State | Postcode | | State | Postcode |
|---|---|---|---|---|
| NSW | 2100 | | SA | 5000 |
| ACT | 2600 | | WA | 6000 |
| VIC | 3000 | | TAS | 7000 |
| QLD | 4000 | | NT | 0800 |

## Each time, for each state

1. In Chrome or Edge open <https://www.liquorland.com.au>, then open the developer tools (**Cmd+Option+I**) and go to the
   **Network** tab. Tick **Preserve log**. In the filter box type `products/ll` (it hides everything else).
2. **Set the state.** Click **Set shopping method**, type the state's postcode, **pick the suggestion** from the list,
   **Save location**, choose the first store listed, then **Save & continue**. The prices on the page are now that state's.
3. Go to **Beer & Cider → Beer** (<https://www.liquorland.com.au/beer-and-cider/beer>). Click **Show 80 results per page**, then
   the next-page button at the bottom until there isn't one (about 13 pages). In the Network tab you should now see one
   `beer-and-cider` row per page.
4. **Save the HAR:** in the Network tab click the download arrow (**Export HAR**), choose **sanitized** if asked (it strips
   your cookies; the product data is kept), and save it as e.g. `~/Downloads/liquorland-nsw.har`.
5. **Import it.** First check, then send:
   ```bash
   .venv/bin/python scripts/import_ll_har.py ~/Downloads/liquorland-nsw.har --dry-run
   .venv/bin/python scripts/import_ll_har.py ~/Downloads/liquorland-nsw.har
   ```
   You can pass several files at once (`~/Downloads/liquorland-*.har`), or one HAR that contains more than one state.

The dry run prints one line per state it found, for example `NSW: 988 of 988 products in 13 pages, saved 2026-10-19 09:30 UTC`.
The state comes from the pages themselves, so a file can't be filed under the wrong state. If it says `only 480 of 988` you
didn't page to the end: page on and save again (or pass `--force`).

## Where it goes

- With `BEEROO_DATA_REMOTE` set in `~/.config/beeroo/env` (it is), the pages go to the cloud job's inbox on the `data`
  branch; the next daily cloud run (4 times a day) adds them to the database and the site. If the cloud can't be reached
  the pages wait in `data/outbox/liquorland/`; run the import again to retry.
- Without it, or with `--local`, they go straight into `data/beeroo.sqlite3`.
- Safe to repeat: importing the same file twice adds no new prices. The observation time is when your browser made the
  request, so it shows as that fresh on the site.

## Notes

- A HAR file contains your browser's requests. Choose the **sanitized** export, don't share the file, and delete it afterwards.
- Liquorland's `robots.txt` disallows `/api/` for automated agents; this is your own ordinary browsing, but its terms of use
  may still restrict copying or republishing price data. They haven't been reviewed; check before publishing the site publicly.
- No console snippet is needed. The importer also accepts a single saved JSON response (right-click a `beer-and-cider` row →
  Copy → Copy response, paste into a plain-text file), but the HAR is easier.
- Ask Coles for a data feed (the affiliate/partnership email) so none of this is needed.
