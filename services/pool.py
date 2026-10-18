"""The discovery pool: about a thousand well-reviewed Steam games with their
user tags, player modes and measured playtime (data/pool.json, built by
``scripts/build_data.py pool``).

Discovery, "similar games" and the deals value score all draw on it, so
those features need no per-request API calls beyond a live price check.
"""

from __future__ import annotations

import json
import math
import os
import threading
from collections import Counter
from typing import Any

from config import Config
from services import tags, vibes

POOL_PATH = os.path.join(Config.DATA_DIR, "pool.json")

_state: dict[str, Any] | None = None
_lock = threading.Lock()


def _load() -> dict[str, Any]:
    try:
        with open(POOL_PATH, encoding="utf-8") as f:
            raw = json.load(f)
    except FileNotFoundError:
        raw = {"built": None, "games": []}
    # Bundled tag names: no network needed to score the pool.
    return build(raw, tags.bundled() or tags.names())


def build(raw: dict[str, Any], names: dict[int, str]) -> dict[str, Any]:
    """Index a pool snapshot: vibe scores, tag vectors and lookups."""
    games = raw.get("games") or []

    df = Counter(tid for g in games for tid, _ in g["tags"])
    n = max(len(games), 1)
    idf = {tid: math.log((1 + n) / (1 + count)) + 1 for tid, count in df.items()}

    for g in games:
        g["tags"] = [tuple(t) for t in g["tags"]]
        scores = vibes.score_tags(g["tags"], names)
        g["vibes"] = {k: v["score"] for k, v in scores.items()}
        g["vibe_why"] = {k: v["why"] for k, v in scores.items()}
        g["vec"] = vector(g["tags"], idf)
        g["norm"] = math.sqrt(sum(w * w for w in g["vec"].values())) or 1.0
        g["tag_names"] = [names[t] for t, _ in sorted(g["tags"], key=lambda t: -t[1]) if t in names]

    return {
        "built": raw.get("built"),
        "games": games,
        "by_id": {g["appid"]: g for g in games},
        "idf": idf,
        "names": names,
    }


def _get() -> dict[str, Any]:
    global _state
    if _state is None:
        with _lock:
            if _state is None:
                _state = _load()
    return _state


def reset(state: dict[str, Any] | None = None) -> None:
    """Forget the loaded pool, or swap in a prepared one (tests)."""
    global _state
    _state = state


def vector(item_tags, idf: dict[int, float]) -> dict[int, float]:
    """Tag vector: each tag's share of the top tag's votes, times its rarity."""
    pairs = list(item_tags)
    if not pairs:
        return {}
    top = max(w for _, w in pairs) or 1
    default = max(idf.values(), default=1.0)
    return {tid: (w / top) * idf.get(tid, default) for tid, w in pairs}


def games() -> list[dict[str, Any]]:
    return _get()["games"]


def get(appid: int) -> dict[str, Any] | None:
    return _get()["by_id"].get(int(appid))


def idf() -> dict[int, float]:
    return _get()["idf"]


def tag_name(tid: int) -> str | None:
    return _get()["names"].get(tid)


def built() -> int | None:
    return _get()["built"]


def stats(g: dict[str, Any]) -> dict[str, Any]:
    """Review stats in the shape :mod:`services.value_score` expects."""
    return {
        **(g["hours"] or {}),
        "positivity": (g["review_pct"] or 0) / 100,
        "total_reviews": g["review_count"],
    }


def has_hours(g: dict[str, Any]) -> bool:
    """Whether we've measured this game's playtime yet."""
    return effective_hours(g) > 0


def effective_hours(g: dict[str, Any]) -> float:
    h = g["hours"] or {}
    return max(h.get("p50_all", 0.0), 0.7 * h.get("p75_recent", 0.0), h.get("p50_recent", 0.0))
