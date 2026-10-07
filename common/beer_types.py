"""Display name and beer type for a product, from its name (and ABV when known).

Neither is stored by the retailers in a form we can rely on, so both are best-effort
and deliberately conservative: a product that matches nothing is "Lager" (most
unlabelled mainstream beers are), and only clear keywords move it elsewhere.
"""
import re

# Shown in this order in the type dropdown.
TYPES = [
    "Lager", "Pilsner", "Pale Ale", "IPA", "Ale", "Stout & Porter", "Wheat Beer",
    "Sour & Wild", "Cider", "Ginger Beer", "Non-Alcoholic", "Other",
]

_NON_ALC_ABV = 0.5

# (type, regex) in priority order; the first match wins.
_RULES = [
    ("Non-Alcoholic", r"\bnon[- ]?alc|alco(?:hol)?[- ]?free|\b0\.0\b|\b0%|\bzero zero\b|\bdealcoholi[sz]ed\b|\bunfiltered 0\b"),
    ("Cider", r"\bcider\b|\bperry\b|\bhard apple\b"),
    ("Ginger Beer", r"\bginger beer\b|\bginger\b"),
    ("Other", r"\bseltzer\b|\bvodka\b|\bgin\b|\bspritz\b|\bmargarita\b|\bcocktail\b|\bwhisky\b|\bbourbon\b|\brum\b|\bkombucha\b|\bhard (?:lemonade|iced tea|tea|soda)\b"),
    ("Stout & Porter", r"\bstout\b|\bporter\b"),
    ("Sour & Wild", r"\bsour\b|\bgose\b|\blambic\b|\bgueuze\b|\bkriek\b|\bberliner\b|\bwild ale\b"),
    ("Wheat Beer", r"\bwheat\b|\bweizen\b|\bweiss\b|\bweisse\b|\bwit(?:bier)?\b|\bhefe\b|\bwhite ale\b|\bwhite beer\b"),
    ("IPA", r"\bipa\b|\bindia pale\b|\bneipa\b|\bdipa\b|\btipa\b"),
    ("Pale Ale", r"\bpale ale\b|\bxpa\b|\bapa\b|\bgolden ale\b|\bsummer ale\b|\bsession ale\b|\bpacific ale\b|\bpale\b"),
    ("Ale", r"\bale\b|\bamber\b|\bbitter\b|\bbrown\b|\bred\b|\bsaison\b|\bbelgian\b|\bbarleywine\b|\bdubbel\b|\btripel\b|\bquad(?:rupel)?\b|\bscotch\b|\besb\b"),
    ("Pilsner", r"\bpilsner\b|\bpilsener\b|\bpils\b|\bpilsen\b"),
    ("IPA", r"\bhazy\b|\bjuicy\b"),
]
_COMPILED = [(t, re.compile(rx, re.I)) for t, rx in _RULES]

_STRIP = [
    re.compile(r"\b\d+\s*x\s*\d+(?:\.\d+)?\s*(?:ml|l|litres?)\b", re.I),     # 10x375ml
    re.compile(r"\b\d+(?:\.\d+)?\s*(?:ml|l|litres?)\b", re.I),               # 375ml, 1.25L
    re.compile(r"\b\d+\s*(?:pack|pk|pcs?|pieces?)\b", re.I),                 # 10 Pack, 6pk
    re.compile(r"\b(?:case|carton|slab|block)\s+of\s+\d+\b", re.I),          # case of 24
    re.compile(r"\b\d+\s*x\b", re.I),                                        # 24 x
]


def display_name(name):
    """The product name without its pack size and volume (shown separately on the
    card): "Amplys 6.9% Hard Apple Cider Cans 10x375ml" -> "Amplys 6.9% Hard Apple Cider Cans"."""
    cleaned = name or ""
    for rx in _STRIP:
        cleaned = rx.sub(" ", cleaned)
    cleaned = re.sub(r"\s+", " ", cleaned)
    cleaned = re.sub(r"^[\s\-–,/]+|[\s\-–,/]+$", "", cleaned)
    cleaned = re.sub(r"\(\s*\)", "", cleaned).strip()
    return cleaned or (name or "").strip()


def beer_type(name, abv=None):
    if abv is not None and abv <= _NON_ALC_ABV:
        return "Non-Alcoholic"
    text = name or ""
    for kind, rx in _COMPILED:
        if rx.search(text):
            return kind
    return "Lager"
