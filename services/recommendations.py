"""Similar games: cosine similarity on Steam user-tag vectors.

Each game's tags are weighted by how many votes they got (relative to its
strongest tag) and by how rare the tag is across the pool, so "Singleplayer"
or "Indie" count for far less than "Souls-like" or "Automation".
"""

from __future__ import annotations

from typing import Any

from services import pool, steam
from services.http import UpstreamError
from services.search import _normalize

MIN_SIMILARITY = 0.25


def _source(appid: int) -> tuple[dict[int, float] | None, bool]:
    """The source game's tag vector, and whether it's adult-tagged itself."""
    g = pool.get(appid)
    if g:
        return g["vec"], pool.is_adult(g)
    try:
        item = steam.store_items([appid]).get(appid)
    except UpstreamError:
        return None, False
    if not item or not item["tags"]:
        return None, False
    top = [pool.tag_name(t) for t, _ in sorted(item["tags"], key=lambda t: -t[1])[:5]]
    return pool.vector(item["tags"], pool.idf()), any(n in pool.ADULT_TAGS for n in top)


def get_recommendations(appid: int | str, limit: int = 8) -> list[dict[str, Any]]:
    appid = int(appid)
    vec, source_adult = _source(appid)
    if not vec:
        return []
    norm = sum(w * w for w in vec.values()) ** 0.5 or 1.0

    scored = []
    for g in pool.games():
        if g["appid"] == appid:
            continue
        if pool.is_adult(g) and not source_adult:
            continue
        shared = vec.keys() & g["vec"].keys()
        if not shared:
            continue
        dot = sum(vec[t] * g["vec"][t] for t in shared)
        sim = dot / (norm * g["norm"])
        if sim >= MIN_SIMILARITY:
            scored.append((sim, g, shared))
    scored.sort(key=lambda t: -t[0])

    out = []
    picked: list[str] = []
    for sim, g, shared in scored:
        if len(out) >= limit:
            break
        # Skip other editions of a game already listed ("Dark Souls II" vs
        # "Dark Souls II: Scholar of the First Sin").
        norm = _normalize(g["name"])
        if any(norm.startswith(p) or p.startswith(norm) for p in picked):
            continue
        picked.append(norm)
        top_shared = [
            t for t in sorted(shared, key=lambda t: -(vec[t] * g["vec"][t]))
            if pool.tag_name(t) not in pool.ADULT_TAGS
        ][:3]
        out.append({
            "appid": g["appid"],
            "name": g["name"],
            "header": g["header"],
            "capsule": g["capsule"],
            "review_pct": g["review_pct"],
            "similarity": round(sim, 3),
            "match_reason": ", ".join(n for n in (pool.tag_name(t) for t in top_shared) if n),
        })
    return out
