from datetime import datetime, timezone


def get_timestamp():
    return datetime.now(timezone.utc).isoformat()


def deduplicate_products(products):
    seen = set()
    unique_products = []

    for product in products:
        key = (
            product["name"].strip().lower(),
            product["quantity"],
            product["unit"],
        )

        if key in seen:
            continue

        seen.add(key)
        unique_products.append(product)

    return unique_products
