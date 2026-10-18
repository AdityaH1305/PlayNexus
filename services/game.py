"""Assemble everything we know about one game.

The page route and the JSON API both call :func:`load_game`, so they can't
drift apart.  All upstream requests are started before any is awaited.
"""

from __future__ import annotations

import logging
from typing import Any

from services import pricing, reviews, steam, value_score, vibes
from services.http import UpstreamError

log = logging.getLogger(__name__)

STORE_URL = "https://store.steampowered.com/app/{appid}"


def load_game(appid: int) -> dict[str, Any] | None:
    """Return the game bundle, or ``None`` if Steam has no such app.

    Raises :class:`UpstreamError` if Steam can't be reached at all.
    """
    details = steam.appdetails(appid, "us")
    if not details:
        return None

    is_free = bool(details.get("is_free"))
    coming_soon = bool((details.get("release_date") or {}).get("coming_soon"))
    price_futures = None if is_free else pricing.submit_regional(appid)
    review_futures = reviews.submit_review_calls(appid)

    stats = reviews.assemble_review_stats(review_futures)
    prices = pricing.assemble_regional(price_futures, is_free=is_free)

    try:
        vibe_info = vibes.score_vibes_for_app(appid)
    except Exception:  # vibes are a nice-to-have; never fail the page for them
        log.exception("vibe scoring failed for %s", appid)
        vibe_info = {"top": []}

    usd = 0.0 if is_free else prices["reference_usd"]
    score, label, breakdown = value_score.compute_value_score(usd, stats, is_free=is_free)

    return {
        "appid": appid,
        "name": details.get("name") or f"App {appid}",
        "details": details,
        "is_free": is_free,
        "coming_soon": coming_soon,
        "header_image": details.get("header_image"),
        "store_url": STORE_URL.format(appid=appid),
        "prices": prices,
        "usd_price": usd,
        "reviews": stats,
        "value": {"score": score, "label": label, "breakdown": breakdown},
        "vibes": vibe_info,
    }


def safe_load_game(appid: int) -> dict[str, Any] | None:
    """Like :func:`load_game` but returns ``None`` on upstream failure too."""
    try:
        return load_game(appid)
    except UpstreamError:
        return None
