"""Price alerts: does a saved alert fire against the latest prices?

An alert is {id, email, product_id, state, pack, target, include_member, last_notified_price, token}.
`pack` is "<pack_type>:<units>" (e.g. "case:24"). With a `target` it fires when the cheapest matching
price at any retailer is at or below it; without one, when that price is the lowest we've recorded
(needs 2+ observations). It doesn't fire again for the same or a higher price than the last email.
"""
from app import queries


class Matcher:
    """Loads each state's products once, however many alerts are checked."""

    def __init__(self, conn, now=None):
        self.conn, self.now, self._by_state = conn, now, {}

    def _products(self, state):
        if state not in self._by_state:
            locations = queries.resolve_locations(self.conn, state)
            self._by_state[state] = {p["id"]: p for p in queries.load_products(self.conn, locations, self.now)}
        return self._by_state[state]

    def check(self, alert):
        """The deal that should be emailed, or None."""
        product = self._products(alert["state"]).get(alert["product_id"])
        hit = best_for_pack(product, alert["pack"], alert.get("include_member", True)) if product else None
        if not hit:
            return None
        target = alert.get("target")
        if target is not None:
            if hit["price"] > target:
                return None
        elif not self._is_lowest_seen(alert, hit):
            return None
        last = alert.get("last_notified_price")
        if last is not None and hit["price"] >= last:
            return None
        return {**hit, "product_name": product["name"], "product_id": product["id"]}

    def _is_lowest_seen(self, alert, hit):
        detail = queries.product_detail(self.conn, alert["product_id"], alert["state"], self.now)
        series = (detail or {}).get("history", {}).get(hit["retailer"], {})
        pack_type, units = alert["pack"].split(":")
        for key, s in series.items():
            if key.startswith(f"{pack_type}:{units}:") and s["current"] == hit["price"]:
                # at its record low, and it has been dearer before (a price that never moved isn't news)
                return s["current"] <= s["min"] and s["max"] > s["current"]
        return False


def best_for_pack(product, pack, include_member=True):
    """The cheapest current price for this pack across retailers, or None."""
    pack_type, units = pack.split(":")
    best = None
    for retailer, entry in product["retailers"].items():
        for o in entry["options"]:
            if o["pack_type"] != pack_type or str(o["units"]) != units:
                continue
            if o["member_only"] and not include_member:
                continue
            if best is None or o["price"] < best["price"]:
                best = {"retailer": retailer, "retailer_name": queries.RETAILER_NAMES.get(retailer, retailer),
                        "price": o["price"], "label": o["label"], "member_only": o["member_only"],
                        "url": entry["url"], "unit_price": o["unit_price"]}
    return best
