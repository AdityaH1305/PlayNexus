"""The "Try …" suggestions under the home page search box.

Every visit should bring up new games, but the CDN serves everyone the same
cached page. So each render picks a batch of sets, and a few lines of script
in the page show a random one, never the set that tab showed last. Without
JavaScript the first set shows.

Most picks are indies that aren't megahits: a gem from data/gems.json (built
by ``build_data.py gems``), or now and then an indie selling well this week.
The rest are popular, well-loved games: one of the big releases on this
week's top sellers, or an all-time favorite from the discovery pool. Nothing
is chosen by player count, which would only ever surface the same
free-to-play giants.
"""

from __future__ import annotations

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

SETS = 20  # sets sent with each render
POPULAR_SHARE = 0.3  # chance a pick is a popular, well-loved game rather than an indie
TRENDING_SHARE = 0.1  # chance an indie pick is one selling well this week rather than a gem
RECENT_SHARE = 0.4  # chance a popular pick is a recent release rather than an all-time favorite
ONCE_PER_SET = {"trending", "recent"}  # short lists that would otherwise crowd the sets
MAX_NAME = 45  # characters; longer names crowd the line

TRENDING_MIN_PCT = 90
TRENDING_REVIEWS = (200, 100_000)  # selling well, but not a megahit
GEM_MIN_PCT = 94
GEM_REVIEWS = (1_000, 70_000)
HIT_MIN_PCT = 90
HIT_MIN_REVIEWS = 5_000
HIT_AGE_DAYS = (7, 540)
FAVORITE_MIN_PCT = 92
FAVORITE_MIN_REVIEWS = GEM_REVIEWS[1]  # more than any gem

WHY = {
    "trending": "Popular indie this week",
    "gem": "Indie gem, {pct}% positive",
    "recent": "A big recent release, {pct}% positive",
    "favorite": "A fan favorite, {pct}% positive",
}

FALLBACK = [
    {"appid": 1145360, "name": "Hades", "why": "An indie favorite"},
    {"appid": 413150, "name": "Stardew Valley", "why": "An indie favorite"},
    {"appid": 1086940, "name": "Baldur's Gate 3", "why": "A modern classic"},
]


def _top_tags(tag_weights, n: int) -> list[int]:
    return [tid for tid, _ in sorted(tag_weights, key=lambda t: -t[1])[:n]]


def _clean_name(name: str) -> str:
    """The name as it reads in a sentence: no ™ or ® marks."""
    return " ".join(name.replace("™", "").replace("®", "").replace("©", "").split())


def _fits(name: str) -> bool:
    """Short enough for the line, and in Latin script so it reads as part of
    an English sentence."""
    name = _clean_name(name)
    return len(name) <= MAX_NAME and all(ord(c) < 0x250 or 0x2000 <= ord(c) <= 0x206F for c in name)


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


def _favorites() -> list[dict[str, Any]]:
    """Paid games from the discovery pool with a huge, nearly all positive
    following: Terraria, Hades, Baldur's Gate 3 and the like."""
    return [
        {"appid": g["appid"], "name": g["name"], "review_pct": g["review_pct"]}
        for g in pool.games()
        if not g["is_free"] and g["price"] and not pool.is_adult(g)
        and (g["review_pct"] or 0) >= FAVORITE_MIN_PCT and g["review_count"] > FAVORITE_MIN_REVIEWS
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
        if INDIE_TAG in top and pct >= TRENDING_MIN_PCT and TRENDING_REVIEWS[0] <= count <= TRENDING_REVIEWS[1]:
            indies.append(entry)
        elif pct >= HIT_MIN_PCT and count >= HIT_MIN_REVIEWS and \
                HIT_AGE_DAYS[0] * DAY <= now - released <= HIT_AGE_DAYS[1] * DAY:
            hits.append(entry)  # indies too big to be niche land here
    hits.sort(key=lambda g: -g["review_pct"])  # stable: ties keep best-seller order
    return {"indies": indies, "hits": hits}


def _order(rng: random.Random) -> list[str]:
    """Where to look for one pick, first choice first: the kind the dice
    chose, then the others in case those lists have run dry."""
    indie = ["trending", "gem"] if rng.random() < TRENDING_SHARE else ["gem", "trending"]
    popular = ["recent", "favorite"] if rng.random() < RECENT_SHARE else ["favorite", "recent"]
    return popular + indie if rng.random() < POPULAR_SHARE else indie + popular


def _draw(rng: random.Random, games: list[dict[str, Any]], weights: list[int] | None,
          used: set[int]) -> dict[str, Any] | None:
    """A random game from *games* that isn't in *used*, by *weights*."""
    options = [(g, w) for g, w in zip(games, weights or [1] * len(games)) if g["appid"] not in used]
    if not options:
        return None
    return rng.choices([g for g, _ in options], weights=[w for _, w in options])[0]


def pick_sets(count: int = SETS, rng: random.Random | None = None) -> list[list[dict[str, Any]]]:
    """Up to *count* different sets of suggestions, ``[[{appid, name, why}, …], …]``.
    Never empty: with nothing else to go on, it's the classic trio."""
    rng = rng or random.Random()
    try:
        live = _live_candidates()
    except UpstreamError:
        live = {"indies": [], "hits": []}
    def usable(games: list[dict[str, Any]]) -> list[dict[str, Any]]:
        return [g for g in games if _fits(g["name"])]

    gems = usable(_gems())
    # Gems out in the last three years are three times as likely to come up.
    recent_cut = time.time() - 3 * 365 * DAY
    sources = {
        "trending": (usable(live["indies"])[:3], None),
        "gem": (gems, [3 if (g.get("release") or 0) >= recent_cut else 1 for g in gems]),
        "recent": (usable(live["hits"])[:6], None),
        "favorite": (usable(_favorites()), None),
    }

    def one_set() -> list[dict[str, Any]]:
        picks: list[dict[str, Any]] = []
        used: set[int] = set()
        spent: set[str] = set()
        for _ in range(3):
            for kind in _order(rng):
                g = None if kind in spent else _draw(rng, *sources[kind], used)
                if g:
                    picks.append({"appid": g["appid"], "name": _clean_name(g["name"]),
                                  "why": WHY[kind].format(pct=g.get("review_pct"))})
                    used.add(g["appid"])
                    if kind in ONCE_PER_SET:
                        spent.add(kind)
                    break
        return picks

    sets: list[list[dict[str, Any]]] = []
    seen: set[frozenset[int]] = set()
    for _ in range(count * 3):  # short lists run out of new sets; stop trying eventually
        s = one_set()
        key = frozenset(p["appid"] for p in s)
        if len(s) >= 2 and key not in seen:
            seen.add(key)
            sets.append(s)
            if len(sets) == count:
                break
    return sets or [list(FALLBACK)]
