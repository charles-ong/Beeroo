"""Scrape Dan Murphy's beer for a postcode and store prices in SQLite.

    python main.py --postcode 3000
    python main.py --postcode 2000 --headless   # tries headless, falls back to headed
"""
import argparse
import asyncio
import os

from playwright.async_api import async_playwright

from common import db
from common.records import utcnow
from scrapers import dan_murphys

DEFAULT_DB = "data/beeroo.sqlite3"


def health_report(location, products, errors, expected_total):
    n = len(products)
    pct = lambda count: f"{100 * count / n:.0f}%" if n else "n/a"
    with_abv = sum(p.listing.abv is not None for p in products)
    with_vol = sum(p.listing.unit_volume_ml is not None for p in products)

    lines = [
        f"Store: {location.store_name} ({location.suburb}, {location.state} "
        f"{location.postcode}) key={location.location_key}",
        f"Products parsed: {n} (site reports {expected_total})",
        f"With ABV: {with_abv} ({pct(with_abv)}); "
        f"with volume: {with_vol} ({pct(with_vol)})",
        f"Rejected: {len(errors)}",
    ]
    lines += [f"  - {sku}: {reason}" for sku, reason in errors[:10]]
    return "\n".join(lines)


async def run(postcode, headless, max_pages, executable_path):
    modes = [True, False] if headless else [False]

    async with async_playwright() as p:
        for mode in modes:
            browser = await p.chromium.launch(
                headless=mode, executable_path=executable_path
            )
            context = await browser.new_context(
                viewport={"width": 1440, "height": 900},
                locale="en-AU",
                timezone_id="Australia/Sydney",
            )

            try:
                page = await context.new_page()
                return await dan_murphys.scrape(page, postcode, max_pages)
            except dan_murphys.Blocked as e:
                print(f"{'Headless' if mode else 'Headed'} run blocked: {e}")
            finally:
                await browser.close()

    raise dan_murphys.Blocked("blocked in every mode tried; stopping")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--postcode", help="4-digit AU postcode (default: site's own default store)")
    parser.add_argument("--headless", action="store_true", help="try headless first, fall back to headed")
    parser.add_argument("--max-pages", type=int, default=dan_murphys.MAX_PAGES)
    parser.add_argument("--db", default=DEFAULT_DB)
    parser.add_argument(
        "--browser-path",
        default=os.environ.get("BEEROO_CHROMIUM_PATH"),
        help="custom Chromium executable (or set BEEROO_CHROMIUM_PATH)",
    )
    args = parser.parse_args()

    if args.postcode and not (args.postcode.isdigit() and len(args.postcode) == 4):
        parser.error("postcode must be 4 digits")

    location, products, errors, expected = asyncio.run(
        run(args.postcode, args.headless, args.max_pages, args.browser_path)
    )

    conn = db.connect(args.db)
    db.upsert_location(conn, location)
    inserted = db.save_products(conn, products, utcnow())
    conn.close()

    print(health_report(location, products, errors, expected))
    print(f"{inserted} new price observations -> {args.db}")


if __name__ == "__main__":
    main()
