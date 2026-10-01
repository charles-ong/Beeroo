import pytest

from common.postcodes import state_for_postcode


@pytest.mark.parametrize(
    "postcode,state",
    [
        ("2000", "NSW"), ("2606", "ACT"), ("2620", "NSW"), ("2900", "ACT"),
        ("3000", "VIC"), ("4000", "QLD"), ("5000", "SA"), ("6000", "WA"),
        ("7000", "TAS"), ("0800", "NT"), ("0200", "ACT"), ("8001", "VIC"),
        (2606, "ACT"),
    ],
)
def test_state_for_postcode(postcode, state):
    assert state_for_postcode(postcode) == state


@pytest.mark.parametrize("bad", ["", "abc", "123", "12345", "0000", "0100", None])
def test_invalid_postcodes(bad):
    assert state_for_postcode(bad) is None
