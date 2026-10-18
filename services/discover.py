"""Discover: find games in the pool by vibe and practical filters.

Ranking puts games that fit *all* chosen vibes first, then by how strongly
they fit, then by how well reviewed they are.  Prices are checked live (one
batched call) because sales change them daily.
"""

from __future__ import annotations

import math
from typing import Any

from services import pool, steam, value_score, vibes
from services.http import UpstreamError

LENGTHS = {
    "short": ("Under 10 hours", 0.5, 10),
    "medium": ("10 to 40 hours", 10, 40),
    "long": ("40 hours or more", 40, math.inf),
}
PLAYERS = {"single": "Single-player", "coop": "Co-op", "pvp": "PvP"}


def _quality(g: dict[str, Any]) -> float:
    """0–1: positivity, trusted more with more reviews."""
    pct = (g["review_pct"] or 0) / 100
    return pct * min(1.0, math.log10(max(10, g["review_count"])) / 5)


def discover(
    vibe_keys: list[str],
    *,
    max_price: float | None = None,
    min_rating: int | None = None,
    players: str | None = None,
    length: str | None = None,
    deck: bool = False,
    limit: int = 36,
) -> dict[str, Any]:
    keys = [k for k in (vibes.normalize_key(v) for v in vibe_keys) if k][:3]
    lo, hi = (LENGTHS[length][1], LENGTHS[length][2]) if length in LENGTHS else (None, None)

    ranked = []
    for g in pool.games():
        if players and players not in g["players"]:
            continue
        if deck and g["deck"] != 3:
            continue
        if min_rating and (g["review_pct"] or 0) < min_rating:
            continue
        if lo is not None and not (lo <= pool.effective_hours(g) < hi):
            continue
        if keys:
            scores = [g["vibes"].get(k, 0) for k in keys]
            fits = sum(s >= vibes.SHOW_AT for s in scores)
            if not fits:
                continue
            rank = (fits, sum(scores) / len(keys) * 0.7 + _quality(g) * 30)
        else:
            rank = (0, _quality(g) * 100)
        ranked.append((rank, g))
    ranked.sort(key=lambda t: t[0], reverse=True)

    # Live prices for the leading candidates (sales change daily).
    head = ranked[: limit * 3]
    try:
        live = steam.store_items([g["appid"] for _, g in head])
    except UpstreamError:
        live = {}

    results = []
    for (fits, _), g in head:
        item = live.get(g["appid"])
        # Steam's item sometimes omits is_free; the snapshot remembers it.
        is_free = bool(item and item["is_free"]) or (g["is_free"] and not (item and item["price"]))
        price = (item["price"] if item else None) or (
            {"final": g["price"], "initial": g["price"], "discount": 0,
             "final_formatted": f"${g['price']:.2f}", "initial_formatted": ""} if g["price"] else None
        )
        if max_price is not None and not is_free and (not price or price["final"] > max_price):
            continue
        results.append(card(g, keys, is_free=is_free, price=price, fits_all=bool(keys) and fits == len(keys)))
        if len(results) >= limit:
            break
    return {"results": results, "matches": len(ranked), "vibes": keys}


def card(g: dict[str, Any], keys: list[str], *, is_free: bool, price: dict | None, fits_all: bool) -> dict[str, Any]:
    hours = pool.effective_hours(g)
    value = None
    if hours and (is_free or (price and price.get("final"))):
        score, label, _ = value_score.compute_value_score(
            0.0 if is_free else price["final"], pool.stats(g), is_free=is_free)
        value = {"score": score, "label": label}
    shown = keys or sorted(g["vibes"], key=lambda k: -g["vibes"][k])[:2]
    return {
        "appid": g["appid"],
        "name": g["name"],
        "header": g["header"],
        "capsule": g["capsule"],
        "review_pct": g["review_pct"],
        "review_count": g["review_count"],
        "is_free": is_free,
        "price": price,
        "hours": round(hours, 1) if hours else None,
        "deck": g["deck"],
        "value": value,
        "fits_all": fits_all,
        "vibes": [
            {"key": k, "label": vibes.VIBES[k]["label"], "score": g["vibes"].get(k, 0), "why": g["vibe_why"].get(k, [])}
            for k in shown
            if g["vibes"].get(k, 0) >= vibes.SHOW_AT
        ],
    }
