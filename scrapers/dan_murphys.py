from urllib.parse import urlsplit, urlunsplit

from bs4 import BeautifulSoup
from playwright.async_api import TimeoutError as PlaywrightTimeoutError

from common.output import get_timestamp
from common.parsing import (
    extract_product_name,
    extract_price_options,
    parse_price,
    resolve_multi_pack_prices,
)


URL = "https://www.danmurphys.com.au/beer/all"
BASE_URL = "https://www.danmurphys.com.au"

LOAD_ALL_PRODUCTS = True
SCRAPE_METHOD = "text"
MAX_LOAD_MORE_CLICKS = 50


def clean_url(url):
    parts = urlsplit(url)
    return urlunsplit(
        (
            parts.scheme,
            parts.netloc,
            parts.path,
            "",
            "",
        )
    )


def make_product(name, price, url, source):
    return {
        "name": name,
        "price": price["price"],
        "quantity": price["quantity"],
        "unit": price["unit"],
        "url": url,
        "source": source,
        "scraped_at": get_timestamp(),
    }


async def wait_for_products(page):
    try:
        await page.locator(
            "shop-product-card"
        ).first.wait_for(
            state="attached",
            timeout=30000,
        )
    except PlaywrightTimeoutError:
        await page.screenshot(
            path="dan_murphys_debug.png",
            full_page=True,
        )

        with open(
            "dan_murphys_debug.html",
            "w",
            encoding="utf-8",
        ) as f:
            f.write(await page.content())

        raise RuntimeError(
            "No shop-product-card elements appeared."
        )


async def load_all_products(page):
    previous_count = await page.locator(
        "shop-product-card"
    ).count()

    if previous_count == 0:
        raise RuntimeError("No initial products found.")

    print(f"Initial product count: {previous_count}")

    for click_number in range(
        1,
        MAX_LOAD_MORE_CLICKS + 1,
    ):
        button = page.locator(
            ".infinite-loader__load-more-button"
        )

        if await button.count() == 0:
            break

        try:
            if not await button.is_visible():
                break

            await button.click()
            await page.wait_for_timeout(2500)

            current_count = await page.locator(
                "shop-product-card"
            ).count()

            print(
                f"Load More {click_number}: "
                f"{current_count} products"
            )

            if current_count <= previous_count:
                break

            previous_count = current_count

        except Exception as e:
            print(f"Load More failed: {e}")
            break


def scrape_card_lines(lines, url):
    name = extract_product_name(lines)

    if not name:
        return []

    price_options = extract_price_options(
        lines,
        name,
    )

    return [
        make_product(
            name=name,
            price=price,
            url=url,
            source="main_listing",
        )
        for price in price_options
    ]


async def scrape_main_products_text(page):
    products = []
    cards = page.locator("shop-product-card")
    count = await cards.count()

    print(f"Text scraper found {count} cards")

    for i in range(count):
        card = cards.nth(i)

        try:
            raw_text = await card.inner_text()

            lines = [
                line.strip()
                for line in raw_text.splitlines()
                if line.strip()
            ]

            link = card.locator("a").first

            if await link.count() == 0:
                continue

            href = await link.get_attribute("href")

            if not href:
                continue

            url = clean_url(
                href
                if href.startswith("http")
                else BASE_URL + href
            )

            products.extend(
                scrape_card_lines(lines, url)
            )

        except Exception as e:
            print(f"Text scraper error on card {i}: {e}")

    return products


def scrape_main_products_html(html):
    products = []

    soup = BeautifulSoup(
        html,
        "html.parser",
    )

    cards = soup.select("shop-product-card")

    print(f"HTML scraper found {len(cards)} cards")

    for i, card in enumerate(cards):
        try:
            lines = [
                line.strip()
                for line in card.get_text(
                    "\n",
                    strip=True,
                ).splitlines()
                if line.strip()
            ]

            link = card.select_one("a[href]")

            if not link:
                continue

            href = link.get("href")

            if not href:
                continue

            url = clean_url(
                href
                if href.startswith("http")
                else BASE_URL + href
            )

            products.extend(
                scrape_card_lines(lines, url)
            )

        except Exception as e:
            print(f"HTML scraper error on card {i}: {e}")

    return products


async def scrape_current_offers(page):
    products = []

    containers = page.locator(
        "div.product__container"
    )

    count = await containers.count()

    print(
        f"Found {count} current-offer containers"
    )

    for i in range(count):
        container = containers.nth(i)

        try:
            title = container.locator(
                "div.product__titleContainer"
            )

            if await title.count() == 0:
                continue

            name = " ".join(
                line.strip()
                for line in (
                    await title.inner_text()
                ).splitlines()
                if line.strip()
            )

            link = container.locator("a").first

            if await link.count() == 0:
                continue

            href = await link.get_attribute("href")

            if not href:
                continue

            url = clean_url(
                href
                if href.startswith("http")
                else BASE_URL + href
            )

            price_container = container.locator(
                "div.product__prices"
            )

            if await price_container.count() == 0:
                continue

            lines = [
                line.strip()
                for line in (
                    await price_container.inner_text()
                ).splitlines()
                if line.strip()
            ]

            raw_options = []

            for line in lines:
                parsed = parse_price(line)

                if parsed:
                    raw_options.append(parsed)

            price_options = resolve_multi_pack_prices(
                raw_options
            )

            for price in price_options:
                products.append(
                    make_product(
                        name=name,
                        price=price,
                        url=url,
                        source="current_offers",
                    )
                )

        except Exception as e:
            print(f"Current offer error on {i}: {e}")

    return products


async def scrape(page):
    await page.goto(
        URL,
        wait_until="domcontentloaded",
        timeout=60000,
    )

    await page.wait_for_timeout(5000)
    await wait_for_products(page)

    if LOAD_ALL_PRODUCTS:
        print("Loading all products...")
        await load_all_products(page)

    await page.wait_for_timeout(1000)

    if SCRAPE_METHOD == "html":
        print("Scraping using rendered HTML...")
        return scrape_main_products_html(
            await page.content()
        )

    if SCRAPE_METHOD == "text":
        print("Scraping using rendered text...")
        return await scrape_main_products_text(page)

    if SCRAPE_METHOD == "auto":
        print("Trying rendered HTML scraper...")

        products = scrape_main_products_html(
            await page.content()
        )

        if products:
            return products

        print(
            "HTML scraper returned no products. "
            "Falling back to text..."
        )

        return await scrape_main_products_text(page)

    raise ValueError(
        "SCRAPE_METHOD must be 'html', 'text', or 'auto'."
    )


async def scrape_all(page):
    main_products = await scrape(page)
    current_offers = await scrape_current_offers(page)

    return current_offers + main_products
