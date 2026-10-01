from pathlib import Path

import pytest

from common.normalise import legacy_to_scraped_products
from common.parsing import merge_split_price_lines
from scrapers.dan_murphys_legacy import scrape_main_products_html

FIXTURE = Path(__file__).parent / "fixtures" / "dan_murphys_beer_listing.html"


@pytest.fixture(scope="module")
def rows():
    return scrape_main_products_html(FIXTURE.read_text(encoding="utf-8"))


def test_merge_split_price_lines():
    lines = ["Coors", "$61.99", "case (24)", "$22.49", "pack (6)", "Add to cart"]
    assert merge_split_price_lines(lines) == [
        "Coors", "$61.99 case (24)", "$22.49 pack (6)", "Add to cart",
    ]


def test_merge_leaves_already_joined_lines_alone():
    lines = ["$61.99 case (24)", "$5.00", "Add to cart"]
    assert merge_split_price_lines(lines) == lines


def test_first_card_is_san_miguel_with_case_and_pack(rows):
    first = [r for r in rows if "San Miguel" in r["name"]]
    assert {(r["price"], r["quantity"], r["unit"]) for r in first} == {
        (71.99, 24, "case"),
        (25.99, 6, "pack"),
    }
    assert first[0]["name"] == "San Miguel Pale Pilsen Bottles 330mL"


def test_no_review_counts_as_names(rows):
    assert all(not r["name"].startswith("(") for r in rows)


def test_every_row_converts_to_valid_records(rows):
    products, errors = legacy_to_scraped_products(rows)
    assert errors == []
    assert len(products) >= 45
    assert all(p.listing.unit_volume_ml for p in products)


def test_multi_buy_cases_resolve_to_total_units(rows):
    heineken = {
        (r["price"], r["quantity"], r["unit"])
        for r in rows
        if "Heineken Lager Bottles 330mL" in r["name"]
    }
    assert (109.9, 48, "case") in heineken  # $109.90 for 2 x 24
    assert (55.95, 24, "case") in heineken


def test_in_store_single_price_is_kept(rows):
    vb = {
        (r["price"], r["quantity"], r["unit"])
        for r in rows
        if r["name"].startswith("Victoria Bitter Lager Cans")
    }
    assert (5.99, 1, "each") in vb or (5.99, None, "each") in vb
