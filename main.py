import asyncio
import json

from playwright.async_api import async_playwright

from common import db
from common.normalise import legacy_to_scraped_products
from common.output import deduplicate_products
from common.records import utcnow
from scrapers.dan_murphys import scrape_all


HEADLESS = False
OUTPUT_FILE = "data/dan_murphys_beer.json"
DB_FILE = "data/beeroo.sqlite3"


async def main():
    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=HEADLESS
        )

        context = await browser.new_context(
            viewport={
                "width": 1440,
                "height": 900,
            },
            locale="en-AU",
            timezone_id="Australia/Sydney",
        )

        page = await context.new_page()

        try:
            products = await scrape_all(page)
            products = deduplicate_products(products)

            print(
                f"Total unique products: {len(products)}"
            )

            with open(
                OUTPUT_FILE,
                "w",
                encoding="utf-8",
            ) as f:
                json.dump(
                    products,
                    f,
                    indent=2,
                    ensure_ascii=False,
                )

            print(f"Saved to {OUTPUT_FILE}")

            records, errors = legacy_to_scraped_products(products)

            for row, reason in errors:
                print(f"Skipped {row.get('name')!r}: {reason}")

            conn = db.connect(DB_FILE)
            inserted = db.save_products(conn, records, utcnow())
            conn.close()

            print(
                f"{len(records)} listings, {inserted} new price "
                f"observations, {len(errors)} rejected -> {DB_FILE}"
            )

        finally:
            await browser.close()


if __name__ == "__main__":
    asyncio.run(main())
