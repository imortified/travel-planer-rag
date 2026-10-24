"""Bounded, deterministic candidate selection with category coverage and date rotation."""

CANDIDATE_LIMIT = 24
PERIOD_POOL_LIMIT = 100
POLICY = "category-rotation-v1"


def select_candidates(
    pois, mandatory, preferences=None, relevance=None, offset=0, limit=CANDIDATE_LIMIT
):
    preferences, relevance = preferences or {}, relevance or {}
    selected = sorted((p for p in pois if p["id"] in mandatory), key=lambda p: p["id"])
    if len(selected) > limit:
        raise ValueError("Mandatory POI exceed candidate limit")
    categories = sorted({p["category"] for p in pois}, key=lambda c: (-preferences.get(c, 0), c))
    pools = {}
    for category in categories:
        candidates = sorted(
            (p for p in pois if p["category"] == category and p["id"] not in mandatory),
            key=lambda p: (-relevance.get(p["id"], -1), p["id"]),
        )
        # Keep the strongest semantic matches, rotate the remainder across dates.
        keep = min(2, len(candidates)) if relevance else 0
        anchors, tail = candidates[:keep], candidates[keep:]
        shift = offset % len(tail) if tail else 0
        pools[category] = anchors + tail[shift:] + tail[:shift]
    while len(selected) < limit:
        changed = False
        for category in categories:
            if pools[category] and len(selected) < limit:
                selected.append(pools[category].pop(0))
                changed = True
        if not changed:
            break
    return selected
