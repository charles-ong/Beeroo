# Changing the website

Edit these files, commit and push to `main`; GitHub rebuilds and deploys the site by itself in a minute or two
(the **Deploy site** workflow, `.github/workflows/deploy-site.yml`). No scrape is needed.

| To change | Edit |
|---|---|
| Wording: the subtitle, filter labels, footer text, page title | `app/static/index.html` (the subtitle is the `<p class="tag">` line near the top) |
| Colours, fonts, spacing, the banner | `app/static/style.css` (colours are the variables at the very top, with a dark-mode set under `prefers-color-scheme: dark`) |
| What the buttons and cards do | `app/static/app.js` (page) and `app/static/staticdata.js` (filtering and sorting in the browser) |
| Which beers count as lager, IPA, non-alcoholic... | `common/beer_types.py` |

Two rules that save confusion:

- **Never edit the `site/` folder.** It is a generated copy (`scripts/export_static.py` builds it from `app/static/`
  plus the database) and it is listed in `.gitignore`, which is why your editor greys it out. Anything you change there is
  overwritten the next time it is built, and it is never uploaded to GitHub. Its `index.html` is just whatever was
  exported last on your Mac, so it can be out of date. The live site is built on GitHub, not from that folder.
- **If you change `staticdata.js` sorting or filtering**, the same logic exists in Python (`app/queries.py`); a test
  (`pytest tests/test_static.py`) checks they agree, so run the tests before pushing.

## Preview your change before pushing

```bash
.venv/bin/python scripts/export_static.py --db data/beeroo.sqlite3 --out site && cd site && python3 -m http.server 8000
```
then open <http://localhost:8000>. (`data/beeroo.sqlite3` is your older local copy; the live data is on GitHub's `data` branch.
To preview with the live data: `BEEROO_DATA_REMOTE=https://github.com/charles-ong/Beeroo.git .venv/bin/python scripts/data_branch.py restore --clone /tmp/beeroo-data --into /tmp/beeroo-live`,
then use `--db /tmp/beeroo-live/beeroo.sqlite3` above.)

## When it goes live

The **Deploy site** workflow shows in the Actions tab. It deploys to the Cloudflare Pages project in the
`BEEROO_CF_PROJECT` repository variable, using the same two Cloudflare secrets as the daily job. Browsers may keep the old
page for a short while; a hard refresh (Cmd+Shift+R) shows the new one.
