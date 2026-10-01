"""SQLite storage. Price observations are append-only."""
import sqlite3

SCHEMA = """
CREATE TABLE IF NOT EXISTS products (
    id INTEGER PRIMARY KEY,
    brand TEXT,
    name TEXT NOT NULL,
    abv REAL,
    unit_volume_ml REAL,
    category TEXT,
    abv_source TEXT
);

CREATE TABLE IF NOT EXISTS locations (
    location_key TEXT PRIMARY KEY,
    retailer TEXT NOT NULL,
    state TEXT,
    postcode TEXT,
    store_id TEXT,
    store_name TEXT,
    suburb TEXT
);

CREATE TABLE IF NOT EXISTS listings (
    id INTEGER PRIMARY KEY,
    retailer TEXT NOT NULL,
    retailer_sku TEXT NOT NULL,
    url TEXT NOT NULL,
    name TEXT NOT NULL,
    brand TEXT,
    category TEXT,
    abv REAL,
    unit_volume_ml REAL,
    product_id INTEGER REFERENCES products(id),
    match_confidence REAL,
    first_seen TEXT NOT NULL,
    last_seen TEXT NOT NULL,
    UNIQUE (retailer, retailer_sku)
);

CREATE TABLE IF NOT EXISTS price_observations (
    id INTEGER PRIMARY KEY,
    listing_id INTEGER NOT NULL REFERENCES listings(id),
    location_key TEXT NOT NULL,
    pack_type TEXT NOT NULL,
    units INTEGER NOT NULL,
    member_only INTEGER NOT NULL DEFAULT 0,
    price REAL NOT NULL,
    observed_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_obs_series ON price_observations
    (listing_id, location_key, pack_type, units, member_only, observed_at);
"""


def connect(path=":memory:"):
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.executescript(SCHEMA)
    return conn


def upsert_location(conn, location):
    conn.execute(
        """
        INSERT INTO locations
            (location_key, retailer, state, postcode, store_id, store_name,
             suburb)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT (location_key) DO UPDATE SET
            state = excluded.state,
            postcode = excluded.postcode,
            store_name = excluded.store_name,
            suburb = excluded.suburb
        """,
        (
            location.location_key,
            location.retailer.value,
            location.state,
            location.postcode,
            location.store_id,
            location.store_name,
            location.suburb,
        ),
    )


def upsert_listing(conn, listing, now):
    now = now.isoformat()
    conn.execute(
        """
        INSERT INTO listings
            (retailer, retailer_sku, url, name, brand, category, abv,
             unit_volume_ml, first_seen, last_seen)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT (retailer, retailer_sku) DO UPDATE SET
            url = excluded.url,
            name = excluded.name,
            brand = COALESCE(excluded.brand, brand),
            category = COALESCE(excluded.category, category),
            abv = COALESCE(excluded.abv, abv),
            unit_volume_ml = COALESCE(excluded.unit_volume_ml, unit_volume_ml),
            last_seen = excluded.last_seen
        """,
        (
            listing.retailer.value,
            listing.retailer_sku,
            listing.url,
            listing.name,
            listing.brand,
            listing.category,
            listing.abv,
            listing.unit_volume_ml,
            now,
            now,
        ),
    )
    row = conn.execute(
        "SELECT id FROM listings WHERE retailer = ? AND retailer_sku = ?",
        (listing.retailer.value, listing.retailer_sku),
    ).fetchone()
    return row["id"]


def record_observation(conn, listing_id, obs):
    """Insert unless the latest observation of this series has the same
    price and was taken on the same UTC day. Returns True if inserted."""
    latest = conn.execute(
        """
        SELECT price, observed_at FROM price_observations
        WHERE listing_id = ? AND location_key = ? AND pack_type = ?
          AND units = ? AND member_only = ?
        ORDER BY observed_at DESC, id DESC LIMIT 1
        """,
        (
            listing_id,
            obs.location_key,
            obs.pack_type.value,
            obs.units,
            int(obs.member_only),
        ),
    ).fetchone()

    observed = obs.observed_at.isoformat()

    if (
        latest
        and latest["price"] == obs.price
        and latest["observed_at"][:10] == observed[:10]
    ):
        return False

    conn.execute(
        """
        INSERT INTO price_observations
            (listing_id, location_key, pack_type, units, member_only,
             price, observed_at)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (
            listing_id,
            obs.location_key,
            obs.pack_type.value,
            obs.units,
            int(obs.member_only),
            obs.price,
            observed,
        ),
    )
    return True


def save_products(conn, products, now):
    """Persist ScrapedProducts. Returns number of observations inserted."""
    inserted = 0
    for product in products:
        listing_id = upsert_listing(conn, product.listing, now)
        for obs in product.prices:
            inserted += record_observation(conn, listing_id, obs)
    conn.commit()
    return inserted


def price_history(conn, listing_id, location_key="national"):
    return conn.execute(
        """
        SELECT pack_type, units, member_only, price, observed_at
        FROM price_observations
        WHERE listing_id = ? AND location_key = ?
        ORDER BY observed_at
        """,
        (listing_id, location_key),
    ).fetchall()


def load_listings(conn):
    """All listings as (listing_id, product_id, Listing)."""
    from common.records import Listing, Retailer

    rows = conn.execute("SELECT * FROM listings ORDER BY id").fetchall()
    return [
        (
            row["id"],
            row["product_id"],
            Listing(
                retailer=Retailer(row["retailer"]),
                retailer_sku=row["retailer_sku"],
                url=row["url"],
                name=row["name"],
                brand=row["brand"],
                category=row["category"],
                abv=row["abv"],
                unit_volume_ml=row["unit_volume_ml"],
            ),
        )
        for row in rows
    ]


def save_matches(conn, clusters):
    """Persist clusters as canonical products (idempotent).

    A cluster reuses the product already linked to any of its listings.
    ABV comes from the first member that states it; abv_source records which
    retailer, so the UI can say where an ABV came from.
    """
    from common.matching import consensus_abv

    ids = {
        (row["retailer"], row["retailer_sku"]): (row["id"], row["product_id"])
        for row in conn.execute(
            "SELECT id, retailer, retailer_sku, product_id FROM listings"
        )
    }
    saved = 0

    for cluster in clusters:
        keys = [(r.value, sku) for r, sku, _ in cluster.members]
        known = [ids[k] for k in keys if k in ids]

        if not known:
            continue

        abv, source = consensus_abv(cluster)
        first = cluster.members[0][2]
        product_id = next((pid for _, pid in known if pid), None)

        if product_id is None:
            product_id = conn.execute(
                """
                INSERT INTO products
                    (brand, name, abv, unit_volume_ml, category, abv_source)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    first.brand,
                    first.name,
                    abv,
                    first.unit_volume_ml,
                    first.category,
                    source.value if source else None,
                ),
            ).lastrowid
        else:
            conn.execute(
                """
                UPDATE products SET abv = COALESCE(abv, ?),
                    abv_source = COALESCE(abv_source, ?)
                WHERE id = ?
                """,
                (abv, source.value if source else None, product_id),
            )

        confidence = 1.0 if len(cluster.members) > 1 else None

        for listing_id, _ in known:
            conn.execute(
                "UPDATE listings SET product_id = ?, match_confidence = ? WHERE id = ?",
                (product_id, confidence, listing_id),
            )

        saved += 1

    conn.commit()
    return saved
