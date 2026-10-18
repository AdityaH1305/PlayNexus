"""Assemble everything we know about one game.

The page route and the JSON API both call :func:`load_game`, so they can't
drift apart.  Every upstream request is started before any is awaited, and
only leaf tasks (ones that never wait on other pool tasks) go to the pool.
"""

from __future__ import annotations

import html
import logging
from typing import Any

from services import pricing, reviews, steam, tags, value_score, vibes
from services.http import UpstreamError, executor

log = logging.getLogger(__name__)

STORE_URL = "https://store.steampowered.com/app/{appid}"
DECK_LABELS = {3: "Verified", 2: "Playable", 1: "Unsupported"}


def _store_item(appid: int) -> dict[str, Any] | None:
    try:
        return steam.store_items([appid]).get(appid)
    except UpstreamError:
        return None


def _meta(details: dict[str, Any]) -> dict[str, Any]:
    release = details.get("release_date") or {}
    platforms = details.get("platforms") or {}
    metacritic = details.get("metacritic") or {}
    return {
        "developers": details.get("developers") or [],
        "publishers": details.get("publishers") or [],
        "release": release.get("date") or "",
        "coming_soon": bool(release.get("coming_soon")),
        "metacritic": metacritic.get("score"),
        "metacritic_url": metacritic.get("url"),
        "platforms": [name for key, name in (("windows", "Windows"), ("mac", "macOS"), ("linux", "Linux")) if platforms.get(key)],
        # Developer-supplied; only allow real web links.
        "website": website if (website := (details.get("website") or "")).startswith(("https://", "http://")) else None,
        "genres": [g.get("description") for g in details.get("genres") or [] if g.get("description")],
        "description": html.unescape(details.get("short_description") or ""),
    }


def load_game(appid: int) -> dict[str, Any] | None:
    """Return the game bundle, or ``None`` if Steam has no such app.

    Raises :class:`UpstreamError` if Steam can't be reached at all.
    """
    details = steam.appdetails(appid, "us")
    if not details:
        return None

    is_free = bool(details.get("is_free"))
    meta = _meta(details)
    item_future = executor.submit(_store_item, appid)
    price_futures = None if is_free else pricing.submit_regional(appid)
    review_futures = reviews.submit_review_calls(appid)

    stats = reviews.assemble_review_stats(review_futures)
    prices = pricing.assemble_regional(price_futures, is_free=is_free)
    item = item_future.result()

    try:
        scores = vibes.score_tags(item["tags"]) if item and item["tags"] else {}
        vibe_info = {"scores": {k: v["score"] for k, v in scores.items()}, "top": vibes.top_vibes(scores)}
    except Exception:  # vibes are a nice-to-have; never fail the page for them
        log.exception("vibe scoring failed for %s", appid)
        vibe_info = {"scores": {}, "top": []}

    usd = 0.0 if is_free else prices["reference_usd"]
    score, label, breakdown = value_score.compute_value_score(usd, stats, is_free=is_free)

    worth_it = None
    if not is_free and stats["total_reviews"]:
        worth_it = {
            "great": value_score.worth_it_price(stats, 70),
            "good": value_score.worth_it_price(stats, 55),
        }

    by_region = {}
    for row in prices["regions"]:
        if row.get("usd"):
            s, lbl, b = value_score.compute_value_score(row["usd"], stats)
            by_region[row["code"]] = {"score": s, "label": lbl, "cost_per_hour": b["cost_per_hour"]}

    header = details.get("header_image") or (item or {}).get("header")
    return {
        "appid": appid,
        "name": details.get("name") or (item or {}).get("name") or f"App {appid}",
        "details": details,
        "meta": meta,
        "is_free": is_free,
        "coming_soon": meta["coming_soon"],
        "header_image": header,
        "hero_image": (item or {}).get("hero") or details.get("background_raw") or header,
        "store_url": STORE_URL.format(appid=appid),
        "tags": tags.top_names((item or {}).get("tags") or [], 8),
        "deck": (item or {}).get("deck", 0),
        "deck_label": DECK_LABELS.get((item or {}).get("deck", 0)),
        "review_label": (item or {}).get("review_label") or stats.get("review_label") or "",
        "prices": prices,
        "usd_price": usd,
        "reviews": stats,
        "value": {
            "score": score,
            "label": label,
            "breakdown": breakdown,
            "worth_it": worth_it,
            "by_region": by_region,
        },
        "vibes": vibe_info,
    }


def safe_load_game(appid: int) -> dict[str, Any] | None:
    """Like :func:`load_game` but returns ``None`` on upstream failure too."""
    try:
        return load_game(appid)
    except UpstreamError:
        return None
