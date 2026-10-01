"""Cross-retailer product matching.

Two listings are the same product when, after removing packaging and generic
descriptor words, their name tokens are identical AND they have the same
per-unit volume AND the same packaging (can vs bottle) when both state it AND
their ABVs don't conflict. Anything looser is returned for human review, never
merged silently.
"""
import re
from dataclasses import dataclass, field

GENERIC = {
    "lager", "beer", "bottle", "bottles", "can", "cans", "longneck",
    "longnecks", "stubby", "stubbies", "block", "carton", "pack", "multipack",
    "brewing", "brewery", "co", "company", "the", "and", "of", "ml", "l", "rack",
}
PACKAGING = {
    "can": "can", "cans": "can", "block": "can",
    "bottle": "bottle", "bottles": "bottle", "longneck": "bottle",
    "longnecks": "bottle", "stubby": "bottle", "stubbies": "bottle",
}
ABV_TOLERANCE = 0.2
# Words one retailer adds and another omits for the same product. Anything
# outside this list (black, lower, sugar, flavours, numbers) marks a variant.
DESCRIPTORS = {
    "premium", "original", "cerveza", "alcoholic", "classic", "best",
}
DESCRIPTOR_CONFIDENCE = 0.9


@dataclass(frozen=True)
class Signature:
    tokens: frozenset
    volume_ml: float
    packaging: str  # "can" | "bottle" | ""
    abv: float


def signature(listing):
    name = (listing.name or "").lower().replace("'", "").replace("’", "")
    name = re.sub(r"\d+\s*x\s*\d+(?:\.\d+)?\s*(?:ml|l)\b", " ", name)
    name = re.sub(r"\d+(?:\.\d+)?\s*(?:ml|l)\b", " ", name)
    name = re.sub(r"\d+(?:\.\d+)?\s*%", " ", name)
    name = re.sub(r"\b\d+\s*(?:pack|pk)\b", " ", name)
    words = re.findall(r"[a-z0-9]+(?:\.\d+)?", name)

    packaging = next((PACKAGING[w] for w in words if w in PACKAGING), "")
    tokens = frozenset(w for w in words if w not in GENERIC)

    return Signature(
        tokens=tokens,
        volume_ml=round(listing.unit_volume_ml or 0),
        packaging=packaging,
        abv=listing.abv,
    )


def _abv_conflict(a, b):
    return (
        a is not None and b is not None and abs(a - b) > ABV_TOLERANCE
    )


def compare(sig_a, sig_b):
    """Return (confidence, reason). confidence 1.0 = safe to merge,
    0.5-0.99 = review only, 0 = different products."""
    if not sig_a.tokens or not sig_b.tokens:
        return 0.0, "no name tokens"

    if not sig_a.volume_ml or sig_a.volume_ml != sig_b.volume_ml:
        return 0.0, "volume differs or unknown"

    if sig_a.packaging and sig_b.packaging and sig_a.packaging != sig_b.packaging:
        return 0.0, "packaging differs"

    if _abv_conflict(sig_a.abv, sig_b.abv):
        return 0.0, "abv conflict"

    if sig_a.tokens == sig_b.tokens:
        return 1.0, "identical name tokens"

    small, large = sorted((sig_a.tokens, sig_b.tokens), key=len)

    if small < large:
        if (large - small) <= DESCRIPTORS:
            return DESCRIPTOR_CONFIDENCE, "descriptor subset"

        return round(len(small) / len(large), 2), "name subset (review)"

    union = sig_a.tokens | sig_b.tokens
    jaccard = len(sig_a.tokens & sig_b.tokens) / len(union)

    if jaccard >= 0.6:
        return round(jaccard, 2), "similar name (review)"

    return 0.0, "names differ"


@dataclass
class Cluster:
    members: list = field(default_factory=list)  # [(retailer, sku, Listing)]

    @property
    def retailers(self):
        return {m[0] for m in self.members}


@dataclass
class Overrides:
    """Manual decisions. Keys are (retailer, sku) pairs."""
    merge: list = field(default_factory=list)
    never: set = field(default_factory=set)

    def blocked(self, a, b):
        return frozenset((a, b)) in self.never


def _key(listing):
    return (listing.retailer, listing.retailer_sku)


def _any_blocked(cluster_a, cluster_b, overrides):
    return any(
        overrides.blocked((ra, sa), (rb, sb))
        for ra, sa, _ in cluster_a.members
        for rb, sb, _ in cluster_b.members
    )


def _merge_into(target, other):
    target.members.extend(other.members)
    other.members = []


def match_listings(listings, merge_threshold=1.0, overrides=None):
    """Cluster listings across retailers.

    `listings`: iterable of Listing. Returns (clusters, review) where review
    is [(listing_a, listing_b, confidence, reason)] for near misses.
    A cluster never holds two listings from the same retailer.

    Passes: exact name tokens; manual `merge` overrides; then descriptor-subset
    matches (one side adds only words like "premium"), merged only when both
    sides have exactly one candidate. Ambiguous ones go to review.
    """
    overrides = overrides or Overrides()
    clusters, sigs, review = [], [], []

    for listing in listings:
        sig = signature(listing)
        placed = False

        for cluster, rep_sigs in zip(clusters, sigs):
            if listing.retailer in cluster.retailers:
                continue

            if any(
                overrides.blocked(_key(listing), (r, s))
                for r, s, _ in cluster.members
            ):
                continue

            scores = [compare(sig, s) for s in rep_sigs]

            if all(s[0] >= merge_threshold for s in scores):
                cluster.members.append((listing.retailer, listing.retailer_sku, listing))
                rep_sigs.append(sig)
                placed = True
                break

        if not placed:
            clusters.append(
                Cluster(members=[(listing.retailer, listing.retailer_sku, listing)])
            )
            sigs.append([sig])

    by_key = {}
    for c in clusters:
        for r, s, _ in c.members:
            by_key[(r, s)] = c

    for a, b in overrides.merge:
        ca, cb = by_key.get(a), by_key.get(b)

        if ca is None or cb is None or ca is cb or ca.retailers & cb.retailers:
            continue

        if _any_blocked(ca, cb, overrides):
            continue

        for m in cb.members:
            by_key[(m[0], m[1])] = ca

        _merge_into(ca, cb)

    clusters = [c for c in clusters if c.members]
    sigs = {id(c): [signature(m[2]) for m in c.members] for c in clusters}

    def worst_between(ca, cb):
        scores = [compare(x, y) for x in sigs[id(ca)] for y in sigs[id(cb)]]
        return min(scores, key=lambda s: s[0])

    candidates = {id(c): [] for c in clusters}

    for i, ca in enumerate(clusters):
        for cb in clusters[i + 1:]:
            if ca.retailers & cb.retailers or _any_blocked(ca, cb, overrides):
                continue

            conf, reason = worst_between(ca, cb)

            if conf == DESCRIPTOR_CONFIDENCE:
                candidates[id(ca)].append(cb)
                candidates[id(cb)].append(ca)
            elif 0 < conf < merge_threshold:
                review.append((ca.members[0][2], cb.members[0][2], conf, reason))

    for ca in clusters:
        mine = candidates[id(ca)]

        if not ca.members:
            continue

        if len(mine) == 1:
            cb = mine[0]

            if (
                cb.members
                and candidates[id(cb)] == [ca]
                and not (ca.retailers & cb.retailers)
            ):
                _merge_into(ca, cb)
                continue

        for cb in mine:
            if cb.members and cb is not ca:
                review.append(
                    (ca.members[0][2], cb.members[0][2], DESCRIPTOR_CONFIDENCE,
                     "ambiguous: several candidates (review)")
                )

    clusters = [c for c in clusters if c.members]
    cluster_of = {(r, s): c for c in clusters for r, s, _ in c.members}
    seen, kept = set(), []

    for a, b, conf, reason in review:
        if cluster_of.get(_key(a)) is cluster_of.get(_key(b)):
            continue

        pair = frozenset((_key(a), _key(b)))

        if pair in seen or overrides.blocked(_key(a), _key(b)):
            continue

        seen.add(pair)
        kept.append((a, b, conf, reason))

    return clusters, kept


def load_overrides(path):
    """CSV with header: action,retailer_a,sku_a,retailer_b,sku_b.
    action is `merge` or `never_merge`. Missing file = no overrides."""
    import csv
    from pathlib import Path

    from common.records import Retailer

    out = Overrides()
    path = Path(path)

    if not path.exists():
        return out

    with path.open(newline="") as fh:
        for row in csv.DictReader(fh):
            action = (row.get("action") or "").strip().lower()

            if action not in {"merge", "never_merge"}:
                raise ValueError(f"{path}: unknown action {action!r}")

            a = (Retailer(row["retailer_a"].strip()), row["sku_a"].strip())
            b = (Retailer(row["retailer_b"].strip()), row["sku_b"].strip())

            if action == "merge":
                out.merge.append((a, b))
            else:
                out.never.add(frozenset((a, b)))

    return out


def consensus_abv(cluster):
    """(abv, source_retailer) from the first member that states ABV."""
    for retailer, _, listing in cluster.members:
        if listing.abv is not None:
            return listing.abv, retailer

    return None, None
