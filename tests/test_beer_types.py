import pytest

from common.beer_types import TYPES, beer_type, display_name


@pytest.mark.parametrize("raw,clean", [
    ("Amplys 6.9% Hard Apple Cider Cans 10x375ml", "Amplys 6.9% Hard Apple Cider Cans"),         # the reported example
    ("Mercury Hard Cider 10 Pack Cans 375ml", "Mercury Hard Cider Cans"),
    ("Carlton Draught Longnecks 750ml", "Carlton Draught Longnecks"),
    ("Matso's Ginger Beer Can 330mL 10pk", "Matso's Ginger Beer Can"),
    ("Heineken 24 x 330mL Cans", "Heineken Cans"),
    ("Coopers Original Pale Ale Cans 375ml", "Coopers Original Pale Ale Cans"),
    ("Hahn Super Dry 3.5 Bottle 330mL", "Hahn Super Dry 3.5 Bottle"),                              # ABV-like numbers stay
    ("Great Northern Brewing Co. Super Crisp 3.5% Lager Cans 375mL", "Great Northern Brewing Co. Super Crisp 3.5% Lager Cans"),
    ("Corona Extra 1.25L Bottle", "Corona Extra Bottle"),
    ("Guinness Case of 24 Cans 440ml", "Guinness Cans"),
])
def test_display_name_drops_pack_size_and_volume_only(raw, clean):
    assert display_name(raw) == clean


def test_display_name_never_returns_nothing():
    assert display_name("375ml") == "375ml"
    assert display_name("") == ""
    assert display_name(None) == ""


@pytest.mark.parametrize("name,abv,kind", [
    ("Carlton Draught Longnecks", 4.6, "Lager"),
    ("Xxxx Gold Mid Strength Lager Beer Cans", 3.5, "Lager"),
    ("Corona Extra Bottle", 4.5, "Lager"),                         # no keyword: the default
    ("Reschs Pilsener Cans", 4.4, "Pilsner"),
    ("Praga Premium Pils Can", 4.7, "Pilsner"),
    ("Young Henrys Newtowner Australian Pale Ale Cans", 4.6, "Pale Ale"),
    ("Little Creatures Juicy XPA Can", 4.6, "Pale Ale"),
    ("Hop Nation Hazy Pale Ale Can", 4.8, "Pale Ale"),
    ("Pirate Life IPA 6.8% Can", 6.8, "IPA"),
    ("Mountain Culture Juice Trip Hazy Can", 7.0, "IPA"),
    ("Coopers Sparkling Ale Bottles", 5.8, "Ale"),
    ("Victoria Bitter 3.5% Block Can", 3.5, "Lager"),                # VB is a lager despite its name
    ("Victoria Bitter Lager Bottles", 4.9, "Lager"),
    ("XXXX Bitter Block Can", 4.4, "Ale"),
    ("Amber Lager Can", 4.5, "Lager"),
    ("Guinness Stout Bottle", 4.2, "Stout & Porter"),
    ("Shambles Big Guy Porter Can", 6.0, "Stout & Porter"),
    ("Weihenstephaner Hefe Bottle", 5.4, "Wheat Beer"),
    ("Boatrocker Miss Pinky Raspberry Sour Ale Can", 4.0, "Sour & Wild"),
    ("Amplys 6.9% Hard Apple Cider Cans", 6.9, "Cider"),
    ("Kopparberg Hard Pear Cider Cans", 4.0, "Cider"),
    ("James Squire Ginger Beer Cans", 4.0, "Ginger Beer"),
    ("Stones Ginger Joe Bottle", 8.0, "Ginger Beer"),
    ("Smirnoff Vodka Seltzer Can", 5.0, "Other"),
    ("Asahi 0.0% Bottle", 0.0, "Non-Alcoholic"),
    ("Carlton Zero Can", None, "Non-Alcoholic"),                    # "Zero" on its own means alcohol-free
    ("Carlton Zero Bottle", None, "Non-Alcoholic"),
    ("Carlton Zero Zero Non Alcoholic Beer Bottles", None, "Non-Alcoholic"),
    ("Hahn Ultra Zero Carb Cans", 4.2, "Lager"),                    # ...but "Zero Carb" / "Zero Sugar" are ordinary beers
    ("Tradie Zero Carb Pale Ale Can", None, "Pale Ale"),
    ("Brookvale Union Zero Sugar Ginger Beer Can", None, "Ginger Beer"),
    ("Heineken Zero Sugar Bottle", None, "Lager"),
    ("Hahn Super Dry Zero", 3.5, "Lager"),                          # a known ABV above 0.5 beats the word
    ("Mornington Free Non-Alc Pale Ale Can", None, "Non-Alcoholic"),
    ("Weihenstephaner Alco Free Hefe Btl", None, "Non-Alcoholic"),
    ("Heaps Normal Quiet Xpa Cans", 0.5, "Non-Alcoholic"),         # by ABV
])
def test_beer_type(name, abv, kind):
    assert beer_type(name, abv) == kind


def test_every_type_the_rules_can_return_is_listed_for_the_dropdown():
    produced = {beer_type(n, a) for n, a in [
        ("x lager", 4), ("x pils", 4), ("x pale ale", 4), ("x ipa", 6), ("x ale", 5), ("x stout", 5), ("x weizen", 5),
        ("x sour", 4), ("x cider", 5), ("x ginger beer", 4), ("x", 0.0), ("x seltzer", 5)]}
    assert produced <= set(TYPES)
