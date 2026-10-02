import pytest

from common.postcodes import state_for_postcode
from common.states import STATES, normalise_state, state_list


def test_every_state_and_territory_is_present_with_a_name_and_postcode():
    assert set(STATES) == {"NSW", "ACT", "VIC", "QLD", "SA", "WA", "TAS", "NT"}
    for code, info in STATES.items():
        assert info["name"] and len(info["postcode"]) == 4 and info["postcode"].isdigit(), code


def test_nsw_is_priced_from_2100_as_specified():
    assert STATES["NSW"]["postcode"] == "2100"


def test_each_pricing_postcode_really_is_in_its_own_state():
    """A typo here would send a scraper to the wrong state's stores."""
    for code, info in STATES.items():
        assert state_for_postcode(info["postcode"]) == code, (code, info["postcode"])


def test_state_list_is_sorted_by_name_for_the_dropdown():
    names = [s["name"] for s in state_list()]
    assert names == sorted(names) and names[0] == "Australian Capital Territory"
    assert all(set(s) == {"code", "name", "postcode"} for s in state_list())


@pytest.mark.parametrize("value,expected", [("nsw", "NSW"), (" Vic ", "VIC"), ("WA", "WA"), ("", None),
                                            (None, None), ("2100", None), ("XX", None), ("N S W", None)])
def test_normalise_state(value, expected):
    assert normalise_state(value) == expected


# ---- retailers with no stores in a state ---------------------------------------


def test_dan_murphys_has_no_stores_in_the_nt_and_the_api_says_so_plainly(tmp_path):
    from app.queries import resolve_locations
    from common import db
    from common.states import has_stores

    assert not has_stores("dan_murphys", "NT") and has_stores("dan_murphys", "NSW") and has_stores("bws", "NT")
    conn = db.connect(str(tmp_path / "x.sqlite3"))
    notices = resolve_locations(conn, "NT")["notices"]
    assert "Dan Murphy's has no stores in Northern Territory." in notices
    assert "No BWS prices for Northern Territory yet." in notices          # a real gap still says "yet"
    assert not any("Dan Murphy's prices" in n for n in notices)
