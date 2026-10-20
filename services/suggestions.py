"""Today's "Try …" suggestions under the home page search box.

Mostly well-loved indie games that aren't megahits; every other day, one
of the big recent releases. Never chosen by player count, which would only
ever surface the same free-to-play giants. Picks change once a day (UTC) and
are the same for every visitor that day, so the home page stays cacheable.

Slots:
1. A popular indie on this week's top sellers or new releases.
2. An indie gem from data/gems.json (built by ``build_data.py gems``).
3. On even days a big recent hit from the top sellers; otherwise another gem.
"""

from __future__ import annotations

import datetime as dt
import json
import os
import random
import time
from functools import lru_cache
from typing import Any

from config import Config
from services import pool, steam, tags
from services.http import UpstreamError, ttl_cache

GEMS_PATH = os.path.join(Config.DATA_DIR, "gems.json")
INDIE_TAG = 492
DAY = 86400

TRENDING_MIN_PCT = 90
TRENDING_REVIEWS = (200, 100_000)  # selling well, but not a megahit
GEM_MIN_PCT = 94
GEM_REVIEWS = (1_000, 70_000)
HIT_MIN_PCT = 90
HIT_MIN_REVIEWS = 5_000
HIT_AGE_DAYS = (7, 540)

FALLBACK = [
    {"appid": 1145360, "name": "Hades", "why": "An indie favourite"},
    {"appid": 413150, "name": "Stardew Valley", "why": "An indie favourite"},
    {"appid": 1086940, "name": "Baldur's Gate 3", "why": "A modern classic"},
]


def _top_tags(tag_weights, n: int) -> list[int]:
    return [tid for tid, _ in sorted(tag_weights, key=lambda t: -t[1])[:n]]


def _adult_tags() -> set[int]:
    return {tid for tid, name in tags.bundled().items() if name in pool.ADULT_TAGS}


@lru_cache(maxsize=1)
def _gems() -> list[dict[str, Any]]:
    """Indie gems from data/gems.json, or picked from the discovery pool."""
    try:
        with open(GEMS_PATH, encoding="utf-8") as f:
            games = json.load(f).get("games") or []
        if games:
            return games
    except FileNotFoundError:
        pass
    return [
        {"appid": g["appid"], "name": g["name"], "review_pct": g["review_pct"],
         "review_count": g["review_count"], "release": g["release"]}
        for g in pool.games()
        if INDIE_TAG in _top_tags(g["tags"], 8) and not g["is_free"] and not pool.is_adult(g)
        and (g["review_pct"] or 0) >= GEM_MIN_PCT and GEM_REVIEWS[0] <= g["review_count"] <= GEM_REVIEWS[1]
    ]


@ttl_cache(ttl=3600, fail_ttl=300)
def _live_candidates() -> dict[str, list[dict[str, Any]]]:
    """Popular indies and big recent hits from this week's top sellers and
    the new releases list, best first."""
    ranks = steam.weekly_top_sellers("US", 100).get("ranks") or []
    ids = [int(r["appid"]) for r in ranks if r.get("appid")]
    try:
        new = (steam.featured_categories("us").get("new_releases") or {}).get("items") or []
        ids += [int(e["id"]) for e in new if e.get("id") and e.get("type", 0) == 0]
    except UpstreamError:
        pass
    ids = list(dict.fromkeys(ids))
    items = steam.store_items(ids)
    if ids and not any(items.values()):
        raise UpstreamError("no store data for the top sellers")

    now, adult = time.time(), _adult_tags()
    indies, hits = [], []
    for appid in ids:
        it = items.get(appid)
        if not it or it["type"] != 0 or not it["name"] or it["is_free"] or it["is_coming_soon"]:
            continue
        released = it["release_date"] or 0
        if not released or released > now:
            continue
        top = _top_tags(it["tags"], 10)
        if adult & set(top[:5]):
            continue
        pct, count = it["review_pct"] or 0, it["review_count"]
        entry = {"appid": appid, "name": it["name"], "review_pct": pct}
        if INDIE_TAG in top:
            if pct >= TRENDING_MIN_PCT and TRENDING_REVIEWS[0] <= count <= TRENDING_REVIEWS[1]:
                indies.append(entry)
        elif pct >= HIT_MIN_PCT and count >= HIT_MIN_REVIEWS and \
                HIT_AGE_DAYS[0] * DAY <= now - released <= HIT_AGE_DAYS[1] * DAY:
            hits.append(entry)
    hits.sort(key=lambda g: -g["review_pct"])  # stable: ties keep best-seller order
    return {"indies": indies, "hits": hits}


def todays_picks(today: dt.date | None = None) -> list[dict[str, Any]]:
    """Three suggestions for *today* (UTC): ``[{appid, name, why}]``."""
    today = today or dt.datetime.now(dt.timezone.utc).date()
    rng = random.Random(today.toordinal())
    try:
        live = _live_candidates()
    except UpstreamError:
        live = {"indies": [], "hits": []}
    gems = _gems()
    # Gems out in the last three years are three times as likely to come up.
    recent_cut = dt.datetime(today.year - 3, today.month, min(today.day, 28), tzinfo=dt.timezone.utc).timestamp()

    picks: list[dict[str, Any]] = []
    used: set[int] = set()

    def take(g: dict[str, Any], why: str) -> None:
        picks.append({"appid": g["appid"], "name": g["name"], "why": why})
        used.add(g["appid"])

    def take_gem() -> None:
        options = [g for g in gems if g["appid"] not in used]
        if options:
            weights = [3 if (g.get("release") or 0) >= recent_cut else 1 for g in options]
            g = rng.choices(options, weights=weights, k=1)[0]
            take(g, f"Indie gem, {g['review_pct']}% positive")

    trending = live["indies"][:3]
    if trending:
        take(rng.choice(trending), "Popular indie this week")
    else:
        take_gem()
    take_gem()
    hits = [g for g in live["hits"][:4] if g["appid"] not in used]
    if today.toordinal() % 2 == 0 and hits:
        g = rng.choice(hits)
        take(g, f"A big recent release, {g['review_pct']}% positive")
    else:
        take_gem()

    return picks if len(picks) >= 2 else list(FALLBACK)
