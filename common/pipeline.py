"""Shared post-ingest steps used by scripts and the web app."""
from common import db
from common.matching import match_listings


def run_matching(conn):
    """(Re)run cross-retailer matching over all listings.
    Returns (canonical products saved, clusters spanning >1 retailer, review list)."""
    listings = [item for _, _, item in db.load_listings(conn)]
    clusters, review = match_listings(listings)
    saved = db.save_matches(conn, clusters)
    multi = [c for c in clusters if len(c.retailers) > 1]
    return saved, len(multi), review
