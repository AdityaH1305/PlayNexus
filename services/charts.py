"""Live Steam charts, filled in with one batched store call per list.

Each function returns ``{"updated": unix|None, "rows": [...]}`` where rows
are game cards (see :func:`card`) plus chart-specific fields.  Non-games
(software, playtests, mods) are dropped.
"""

from __future__ import annotations

from typing import Any

from services import steam, tags
from services.http import UpstreamError

GAME_TYPE = 0  # IStoreBrowseService item type for games


def card(item: dict[str, Any]) -> dict[str, Any]:
    """The fields every game list needs, from a :func:`steam.store_items` item."""
    reliable = item["review_count"] >= 10
    return {
        "appid": item["appid"],
        "name": item["name"],
        "header": item["header"],
        "capsule": item["small_capsule"],
        "is_free": item["is_free"],
        "price": item["price"],
        "review_pct": item["review_pct"] if reliable else None,
        "review_label": item["review_label"] if reliable else "",
        "review_count": item["review_count"],
        "tags": tags.top_names(item["tags"], 3),
        "deck": item["deck"],
        "release_date": item["release_date"],
        "coming_soon": item["is_coming_soon"],
    }


def _rows(ranked: list[dict[str, Any]], cc: str = "US") -> list[dict[str, Any]]:
    """Merge chart entries (each with an ``appid``) with store data, games only."""
    items = steam.store_items([int(r["appid"]) for r in ranked], cc)
    rows = []
    for r in ranked:
        it = items.get(int(r["appid"]))
        if not it or not it["name"] or it["type"] != GAME_TYPE:
            continue
        rows.append({**card(it), **{k: v for k, v in r.items() if k != "appid"}})
    return rows


def playing_now(limit: int = 100) -> dict[str, Any]:
    data = steam.concurrent_chart()
    ranked = [
        {"appid": r["appid"], "players": r.get("concurrent_in_game"), "peak": r.get("peak_in_game")}
        for r in (data.get("ranks") or [])[:limit]
    ]
    rows = _rows(ranked)
    for i, row in enumerate(rows, 1):
        row["rank"] = i
    return {"updated": data.get("last_update"), "rows": rows}


def top_sellers(cc: str = "US", limit: int = 50) -> dict[str, Any]:
    data = steam.weekly_top_sellers(cc, limit)
    ranked = [
        {"appid": r["appid"], "last_week_rank": r.get("last_week_rank"), "weeks": r.get("consecutive_weeks")}
        for r in (data.get("ranks") or [])
        if r.get("appid")
    ]
    rows = _rows(ranked, cc)
    for i, row in enumerate(rows, 1):
        row["rank"] = i
    return {"updated": data.get("start_date"), "rows": rows}


def _featured_list(key: str, cc: str = "us") -> dict[str, Any]:
    data = steam.featured_categories(cc)
    entries = (data.get(key) or {}).get("items") or []
    seen: set[int] = set()
    ranked = []
    for e in entries:
        appid = e.get("id")
        if e.get("type", 0) != 0 or not appid or appid in seen:
            continue
        seen.add(appid)
        ranked.append({"appid": appid})
    rows = _rows(ranked, cc.upper())
    for i, row in enumerate(rows, 1):
        row["rank"] = i
    return {"updated": None, "rows": rows}


def new_releases(cc: str = "us") -> dict[str, Any]:
    return _featured_list("new_releases", cc)


def coming_soon(cc: str = "us") -> dict[str, Any]:
    return _featured_list("coming_soon", cc)


CHARTS = {
    "playing": ("Playing now", playing_now),
    "sellers": ("Top sellers", top_sellers),
    "new": ("New releases", new_releases),
    "soon": ("Coming soon", coming_soon),
}


def safe(fn, *args, **kwargs) -> dict[str, Any]:
    """Run a chart function, returning an empty chart if Steam is unreachable."""
    try:
        return fn(*args, **kwargs)
    except UpstreamError:
        return {"updated": None, "rows": [], "error": True}
