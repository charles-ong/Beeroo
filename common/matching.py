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
    "brewing", "brewery", "co", "company", "the", "and", "of", "ml", "l",
}
PACKAGING = {
    "can": "can", "cans": "can", "block": "can",
    "bottle": "bottle", "bottles": "bottle", "longneck": "bottle",
    "longnecks": "bottle", "stubby": "bottle", "stubbies": "bottle",
}
ABV_TOLERANCE = 0.2


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


def match_listings(listings, merge_threshold=1.0):
    """Cluster listings across retailers.

    `listings`: iterable of Listing. Returns (clusters, review) where review
    is [(listing_a, listing_b, confidence, reason)] for near misses.
    A cluster never holds two listings from the same retailer.
    """
    clusters, sigs, review = [], [], []

    for listing in listings:
        sig = signature(listing)
        placed = False

        for cluster, rep_sigs in zip(clusters, sigs):
            if listing.retailer in cluster.retailers:
                continue

            scores = [compare(sig, s) for s in rep_sigs]
            best = max(scores, key=lambda s: s[0])

            if best[0] >= merge_threshold and all(
                s[0] >= merge_threshold for s in scores
            ):
                cluster.members.append((listing.retailer, listing.retailer_sku, listing))
                rep_sigs.append(sig)
                placed = True
                break

            if 0 < best[0] < merge_threshold:
                review.append((cluster.members[0][2], listing, best[0], best[1]))

        if not placed:
            clusters.append(
                Cluster(members=[(listing.retailer, listing.retailer_sku, listing)])
            )
            sigs.append([sig])

    return clusters, review


def consensus_abv(cluster):
    """(abv, source_retailer) from the first member that states ABV."""
    for retailer, _, listing in cluster.members:
        if listing.abv is not None:
            return listing.abv, retailer

    return None, None
