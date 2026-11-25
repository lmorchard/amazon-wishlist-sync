"""Tag vocabulary for harvested wishlist items.

One place, so the decision cannot drift between the extractor, the writer, and
whatever reads these bookmarks later.

The distinction matters: an off-site want is not an Amazon product. Tagging it
`amazon` would claim an association it does not have and poison store queries.
It still deserves a provenance tag, because the fact that it was *found* on an
Amazon wishlist is real information that cannot be reconstructed later.
"""

ROOT_TAG = "wishlist"

AMAZON_TAG = "amazon"
EXTERNAL_PROVENANCE_TAG = "imported:amazon"


def tags_for(item):
    """Return the tag list for an extracted item.

    Never includes a user's own tags — this tool creates bookmarks, it does not
    interpret them.
    """
    if item.get("shape") == "amazon":
        return [ROOT_TAG, AMAZON_TAG]
    return [ROOT_TAG, EXTERNAL_PROVENANCE_TAG]


def describe(item):
    """Human-readable tag summary for dry-run output."""
    return " ".join(tags_for(item))


if __name__ == "__main__":
    sample = [
        {"shape": "amazon", "title": "Shelly 1 Gen4"},
        {"shape": "external", "title": "PNW Tarot"},
    ]
    for s in sample:
        print(f"{s['shape']:9} {describe(s):28} {s['title']}")
