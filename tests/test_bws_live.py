import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from scrapers import bws, bws_live

F = Path(__file__).parent / "fixtures"
NOW = datetime(2026, 10, 1, tzinfo=timezone.utc)
ROBOTS = """
User-agent: *
Sitemap: https://bws.com.au/sitemap/products-sitemap.xml
Disallow: /search
Disallow: /my-profile
Disallow: /my-account/order-history
Disallow: /checkout
Disallow: /customer-reviews
Disallow: /Akamai
"""  # BWS's real robots.txt, fetched 2026-10-01 (rules only)


def snapshot(products):
    return sorted(
        (p.listing.retailer_sku, p.listing.name, p.listing.abv, p.listing.unit_volume_ml,
         tuple(sorted((o.pack_type.value, o.units, o.member_only, o.price) for o in p.prices)))
        for p in products
    )


# ---- robots.txt gate --------------------------------------------------------


@pytest.mark.parametrize("path,allowed", [
    ("/beer/all-beer", True), ("/beer", True), ("/productgroup/beer-bestsellers", True),
    ("/search", False), ("/search?q=beer", False), ("/checkout/cart", False),
    ("/my-account/order-history", False), ("/my-account/profile", True),
    ("/customer-reviews/x", False), ("/Akamai/x", False),
])
def test_robots_rules(path, allowed):
    assert bws_live.path_allowed(ROBOTS, path) is allowed


def test_robots_allow_overrides_a_shorter_disallow():
    txt = "User-agent: *\nDisallow: /beer\nAllow: /beer/all-beer\n"
    assert bws_live.path_allowed(txt, "/beer/all-beer") is True
    assert bws_live.path_allowed(txt, "/beer/craft") is False


def test_robots_other_agents_dont_apply_and_empty_means_allowed():
    assert bws_live.path_allowed("User-agent: googlebot\nDisallow: /\n", "/beer") is True
    assert bws_live.path_allowed("", "/beer") is True
    assert bws_live.path_allowed("User-agent: *\nDisallow:\n", "/beer") is True   # empty Disallow = allow all


# ---- minimal payload --------------------------------------------------------


def test_minimal_payload_parses_identically_and_is_smaller():
    raw = json.loads((F / "bws_2606_products.json").read_text())
    slim = bws.minimal_payload(raw)
    a, ea = bws.parse_bws_payload(raw, "k", NOW)
    b, eb = bws.parse_bws_payload(slim, "k", NOW)
    assert snapshot(a) == snapshot(b) and len(a) == 14
    assert len(json.dumps(slim)) < len(json.dumps(raw)) / 3


def test_minimal_payload_drops_out_of_stock_items_and_personal_fields():
    raw = json.loads((F / "bws_2606_products.json").read_text())
    raw["Items"][0]["Products"][0]["QuantityInTrolley"] = 9
    raw["Items"][0]["Products"][0]["IsWatched"] = True
    slim = bws.minimal_payload(raw)
    text = json.dumps(slim)
    for leaked in ("QuantityInTrolley", "IsWatched", "RichDescription", "ImageTag"):
        assert leaked not in text
    in_stock = [i for i in raw["Items"] if any(p.get("IsAvailable") for p in i["Products"])]
    assert len(slim["Items"]) == len(in_stock) < len(raw["Items"])
    assert len(bws.minimal_payload(raw, only_available=False)["Items"]) == len(raw["Items"])


# ---- collector --------------------------------------------------------------


class FakeResponse:
    def __init__(self, url, payload, method="GET"):
        self.url = url
        self._payload = payload
        self.request = type("R", (), {"method": method})()

    async def json(self):
        return self._payload


def run(coro):
    import asyncio
    return asyncio.run(coro)


def item(parent, available=True):
    return {"PackParentStockCode": parent, "Name": f"n{parent}", "Products": [{"Stockcode": parent, "IsAvailable": available}]}


def test_collector_reads_browse_pages_bundles_and_store_calls():
    c = bws_live.BwsCollector()
    base = "https://api.bws.com.au/apis/ui"
    run(c.on_response(FakeResponse(f"{base}/Browse?pageNumber=1", {"Bundles": [item(1), item(2)], "TotalRecordCount": 745})))
    run(c.on_response(FakeResponse(f"{base}/Browse?pageNumber=2", {"Bundles": [item(2), item(3)], "TotalRecordCount": 745})))
    run(c.on_response(FakeResponse(f"{base}/Products/Bundles/1,2,3", [item(4)])))
    run(c.on_response(FakeResponse(f"{base}/ProductGroup/Products/beer-specials", {"Items": [item(5)]})))
    run(c.on_response(FakeResponse(f"{base}/Address/SetPickupByStoreNo", {"Success": True}, "POST")))
    run(c.on_response(FakeResponse(f"{base}/Trolley", {"ignored": 1})))
    assert sorted(c.items) == [1, 2, 3, 4, 5]             # de-duplicated across pages
    assert c.total == 745 and c.browse_pages == 2
    assert c.preferences == {"Success": True}
    assert len(c.payload()["Items"]) == 5


def test_collector_ignores_bundles_without_products_and_bad_responses():
    c = bws_live.BwsCollector()
    run(c.on_response(FakeResponse("https://api.bws.com.au/apis/ui/Browse", {"Bundles": [{"PackParentStockCode": 1, "Products": []}]})))
    bad = FakeResponse("https://api.bws.com.au/apis/ui/Products/Bundles/1", None)
    bad.json = None  # raises when awaited
    run(c.on_response(bad))
    assert c.items == {}


# ---- real-world data quirks found in the live Canberra scrape -----------------
# (values copied from the real BWS responses for these products)


def _product(name, price, unitqty, liquorsize, stock=7001, abv="4.5%", packtype="Can", promo=None, mult=0, promo_type=None):
    return {
        "Stockcode": stock, "Price": price, "Name": name, "UrlFriendlyName": "x", "IsAvailable": True,
        "PackageSize": liquorsize, "BrandName": "B", "PromotionType": promo_type,
        "FixedPricePromoTag": {"PromotionalPrice": promo or 0, "ProductMultiplier": mult},
        "AdditionalDetails": [
            {"Name": "productunitquantity", "Value": unitqty}, {"Name": "alcohol%", "Value": abv},
            {"Name": "liquorsize", "Value": liquorsize}, {"Name": "webpacktype", "Value": packtype},
        ],
    }


def parse_one(*products):
    item = {"PackParentStockCode": 1, "Name": "n", "Products": list(products)}
    out, errs = bws.parse_bws_payload({"Items": [item]}, "k", NOW)
    assert errs == []
    return out[0]


def pps(p, obs):
    from common.units import price_per_standard_drink
    return price_per_standard_drink(obs.price, obs.units, p.listing.unit_volume_ml, p.listing.abv)


@pytest.mark.parametrize("name,size,price,units", [
    ("Magners Original Irish Cider 10 Pack Cans 330ml", "3300ML", 32.0, 10),
    ("Mountain Culture Status Quo Pale Ale 10 Pack Cans 355ml", "355ML", 53.0, 10),
])
def test_an_n_pack_sold_with_quantity_one_takes_its_count_from_the_name(name, size, price, units):
    p = parse_one(_product(name, price, "1", size))
    (obs,) = p.prices
    assert obs.units == units and p.listing.unit_volume_ml in (330, 355)
    assert 2.0 < pps(p, obs) < 6.0


def test_liquorsize_is_not_trusted_for_singles_whose_item_has_a_big_pack():
    # Real: the single can ($2.50), the 6-pack and the 24-case all report liquorsize "9000ML".
    single = _product("Hahn Superdry 1.8% Cans 375ml", 2.5, "1", "9000ML", stock=1, abv="1.8%")
    six = _product("Hahn Superdry 1.8% Cans 375ml", 12.0, "6", "9000ML", stock=2, abv="1.8%")
    case = _product("Hahn Superdry 1.8% Cans 375ml", 44.45, "24", "9000ML", stock=3, abv="1.8%")
    p = parse_one(single, six, case)
    assert p.listing.unit_volume_ml == 375
    got = {o.units: o.price for o in p.prices}
    assert got == {1: 2.5, 6: 12.0, 24: 44.45}                   # NOT "24 cans for $2.50"


def test_ginja_ninja_single_is_a_single():
    p = parse_one(_product("Tumut River Brewing Co. Ginja Ninja Ginger Beer Cans 375ml", 7.5, "1", "6000ML", abv="5.3%"),
                  _product("Tumut River Brewing Co. Ginja Ninja Ginger Beer Cans 375ml", 76.0, "16", "6000ML", stock=2, abv="5.3%"))
    got = {o.units: o.price for o in p.prices}
    assert got == {1: 7.5, 16: 76.0} and p.listing.unit_volume_ml == 375


def test_a_genuine_single_is_left_alone():
    p = parse_one(_product("Carlton Dry Lager Bottles 330ml", 5.5, "1", "330ML"))
    assert p.listing.unit_volume_ml == 330 and p.prices[0].units == 1


def test_normal_multipack_products_are_unchanged():
    six = _product("Carlton Dry Lager Bottles 330ml", 22.5, "6", "330ML", stock=2)
    one = _product("Carlton Dry Lager Bottles 330ml", 5.5, "1", "330ML", stock=3)
    p = parse_one(six, one)
    assert sorted((o.units, o.price) for o in p.prices) == [(1, 5.5), (6, 22.5)]


def test_pack_in_the_name_does_not_override_a_real_quantity():
    p = parse_one(_product("Heineken 6 Pack Bottles 330ml", 24.0, "6", "330ML"))
    assert p.prices[0].units == 6


def test_names_without_a_pack_count_are_not_misread():
    p = parse_one(_product("4 Pines Brewing Pacific Ale Cans 375ml", 5.5, "1", "375ML"))
    assert p.prices[0].units == 1


def test_volume_falls_back_to_liquorsize_only_when_the_name_has_none():
    p = parse_one(_product("Mystery Beer", 5.0, "1", "500ML"))
    assert p.listing.unit_volume_ml == 500


def test_a_case_with_quantity_one_gets_its_count_from_liquorsize():
    # Real: Travla Mid Strength, webpacktype "Case", $67, quantity "1", liquorsize 11250ML (30 x 375)
    p = parse_one(_product("Travla Mid Strength Lager Cans 375ml", 67.0, "1", "11250ML", packtype="Case", abv="3.5%"))
    (obs,) = p.prices
    assert (obs.units, obs.pack_type.value, obs.price) == (30, "case", 67.0)
    assert 1.5 < pps(p, obs) < 2.5


def test_a_case_of_unknown_size_is_skipped_not_guessed():
    item = {"PackParentStockCode": 1, "Name": "n", "Products": [
        _product("Mystery Lager Cans 375ml", 67.0, "1", "11000ML", packtype="Case")]}   # 29.3x: not a whole number
    out, errs = bws.parse_bws_payload({"Items": [item]}, "k", NOW)
    assert out == [] and errs and errs[0][1] == "no available online prices"


def test_a_carton_multibuy_stamped_on_the_single_can_is_ignored():
    # Real: Victoria Bitter. "2 for $120" appears on the $5.50 can AND the $65 carton.
    can = _product("Victoria Bitter Lager Cans 375ml", 5.5, "1", "375ML", promo=120.0, mult=2)
    carton = _product("Victoria Bitter Lager Cans 375ml", 65.0, "24", "375ML", stock=2, packtype="Case", promo=120.0, mult=2)
    p = parse_one(can, carton)
    got = sorted((o.units, o.price) for o in p.prices)
    assert got == [(1, 5.5), (24, 65.0), (48, 120.0)]            # the 48-can deal survives; "2 cans for $120" does not


def test_genuine_multibuys_are_kept():
    p = parse_one(_product("Hollandia Beer Bottles 650ml", 8.0, "1", "650ML", packtype="Bottle", promo=19.0, mult=3))
    assert sorted((o.units, o.price) for o in p.prices) == [(1, 8.0), (3, 19.0)]


def test_an_app_offer_must_be_cheaper_than_the_shelf_price():
    ok = parse_one(_product("A 330ml", 24.0, "6", "330ML", packtype="Pack", promo=20.0, promo_type="AppBasedOffer"))
    assert any(o.member_only and o.price == 20.0 for o in ok.prices)
    bad = parse_one(_product("A 330ml", 24.0, "6", "330ML", packtype="Pack", promo=30.0, promo_type="AppBasedOffer"))
    assert not any(o.member_only for o in bad.prices)


def test_slimmed_payload_keeps_what_the_quirk_rules_need():
    """webpacktype/liquorsize drive the pack-count rules; slimming must not drop them."""
    items = [
        {"PackParentStockCode": 1, "Name": "a", "Products": [_product("Travla Mid Strength Lager Cans 375ml", 67.0, "1", "11250ML", packtype="Case", abv="3.5%")]},
        {"PackParentStockCode": 2, "Name": "b", "Products": [_product("Magners Original Irish Cider 10 Pack Cans 330ml", 32.0, "1", "3300ML")]},
        {"PackParentStockCode": 3, "Name": "c", "Products": [_product("Hahn Superdry 1.8% Cans 375ml", 2.5, "1", "9000ML", abv="1.8%")]},
    ]
    raw = {"Items": items, "TotalRecordCount": 3}
    a, _ = bws.parse_bws_payload(raw, "k", NOW)
    b, _ = bws.parse_bws_payload(bws.minimal_payload(raw), "k", NOW)
    assert snapshot(a) == snapshot(b)
    assert sorted(o.units for p in b for o in p.prices) == [1, 10, 30]


@pytest.mark.parametrize("name,qty,packtype,price,units,volume", [
    # all copied from real BWS data (Canberra capture, 2026-10-01)
    ("Amplys 6.9% Hard Apple Cider Cans 10x375ml", "1", "Pack", 29.0, 10, 375),
    ("Amplys 6.9% Hard Apple Cider Cans 10x375ml", "3", "Case", 70.0, 30, 375),
    ("Kopparberg Ginger Beer Cans 10x375ml", "1", "Can", 32.0, 10, 375),
    ("Scape Goat Crisp Apple Cider Cans 10x330ml", "3", "Case", 54.0, 30, 330),
    ("Scape Goat Crisp Apple Cider Cans 10x330ml", "1", "Each", 21.0, 10, 330),
    ("Somersby Pear Cider Cans 10x375ml", "3", "Case", 90.0, 30, 375),
])
def test_count_x_volume_names_multiply_the_quantity(name, qty, packtype, price, units, volume):
    p = parse_one(_product(name, price, qty, f"{volume}ML", packtype=packtype, abv="5.0%"))
    (obs,) = p.prices
    assert obs.units == units and p.listing.unit_volume_ml == volume
    assert 1.0 < pps(p, obs) < 4.5                                # was 15-22 before the fix


def test_implausibly_large_counts_are_skipped():
    item = {"PackParentStockCode": 1, "Name": "n", "Products": [_product("Bulk Lager 24x330ml", 99.0, "3", "330ML", packtype="Case")]}
    out, errs = bws.parse_bws_payload({"Items": [item]}, "k", NOW)
    assert out == [] and errs                                     # 24 x 3 = 72 > 60: don't guess


# ---- "N Pack" items: the item's sibling products decide (real data, 2026-10-01) -------


def units_and_prices(*products):
    p = parse_one(*products)
    return sorted((o.units, o.price) for o in p.prices if not o.member_only)


def test_kopparberg_single_can_is_not_mistaken_for_a_ten_pack():
    # Reported edge case: the can is $5 and the 10 pack is $25. All three share the name "...10 Pack Cans 330ml".
    name = "Kopparberg Hard Apple Cider 10 Pack Cans 330ml"
    got = units_and_prices(
        _product(name, 25.0, "10", "330ML", packtype="Pack", stock=975730),
        _product(name, 5.0, "1", "330ML", packtype="Can", stock=120842),
        _product(name, 49.0, "20", "330ML", packtype="Case", stock=975731),
    )
    assert got == [(1, 5.0), (10, 25.0), (20, 49.0)]
    p = parse_one(
        _product(name, 25.0, "10", "330ML", packtype="Pack"), _product(name, 5.0, "1", "330ML", packtype="Can", stock=2))
    assert {o.units: o.pack_type.value for o in p.prices} == {1: "single", 10: "pack"}
    assert pps(p, next(o for o in p.prices if o.units == 1)) == pytest.approx(5.0 / (0.33 * 4.5 * 0.789), rel=1e-6)


def test_order_of_the_products_does_not_matter():
    name = "Kopparberg Hard Apple Cider 10 Pack Cans 330ml"
    items = [_product(name, 25.0, "10", "330ML", packtype="Pack", stock=1),
             _product(name, 5.0, "1", "330ML", packtype="Can", stock=2),
             _product(name, 49.0, "20", "330ML", packtype="Case", stock=3)]
    assert units_and_prices(*items) == units_and_prices(*reversed(items)) == [(1, 5.0), (10, 25.0), (20, 49.0)]


def test_magners_only_product_is_still_the_ten_pack():
    assert units_and_prices(_product("Magners Original Irish Cider 10 Pack Cans 330ml", 32.0, "1", "3300ML", packtype="Can")) == [(10, 32.0)]


def test_mercury_quantities_count_packs():
    name = "Mercury Hard Cider 10 Pack Cans 375ml"
    assert units_and_prices(_product(name, 31.0, "1", "375ML", packtype="Pack", stock=1),
                            _product(name, 98.0, "3", "375ML", packtype="Case", stock=2)) == [(10, 31.0), (30, 98.0)]


def test_mountain_culture_pack_with_a_can_counted_case():
    name = "Mountain Culture Status Quo Pale Ale 10 Pack Cans 355ml"
    assert units_and_prices(_product(name, 53.0, "1", "355ML", packtype="Pack", stock=1),
                            _product(name, 160.0, "30", "355ML", packtype="Case", stock=2)) == [(10, 53.0), (30, 160.0)]


def test_x_volume_names_still_work_with_and_without_a_can_counted_sibling():
    name = "Somersby Pear Cider Cans 10x375ml"
    assert units_and_prices(_product(name, 25.0, "1", "375ML", packtype="Can", stock=1),
                            _product(name, 90.0, "3", "375ML", packtype="Case", stock=2)) == [(10, 25.0), (30, 90.0)]
    # same name, but the item carries an explicit qty-10 product: then qty 1 is a single can
    assert units_and_prices(_product(name, 3.0, "1", "375ML", stock=1),
                            _product(name, 25.0, "10", "375ML", packtype="Pack", stock=2)) == [(1, 3.0), (10, 25.0)]


def test_kopparberg_when_only_the_single_can_is_in_stock():
    """The reported case in NSW: the 10 and 20 packs are out of stock (but still listed), the can is $5."""
    name = "Kopparberg Hard Apple Cider 10 Pack Cans 330ml"
    ten = _product(name, 25.0, "10", "330ML", packtype="Pack", stock=975730)
    can = _product(name, 5.0, "1", "330ML", packtype="Can", stock=120842)
    twenty = _product(name, 49.0, "20", "330ML", packtype="Case", stock=975731)
    ten["IsAvailable"] = twenty["IsAvailable"] = False
    p = parse_one(ten, can, twenty)
    assert [(o.units, o.pack_type.value, o.price) for o in p.prices] == [(1, "single", 5.0)]     # not "10 for $5"
