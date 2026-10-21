"""IsThereAnyDeal: years of US Steam price history for nearly every game.

It powers "Buy now or wait?" and the game page's price chart. It needs a free
API key (``ITAD_API_KEY``) from an app registered on isthereanydeal.com;
without one the verdict is left out and the chart uses our own records.

ITAD's terms ask apps to credit it and not to change its data, so pages that
show these prices say where they come from and link to the game on ITAD.
"""

from __future__ import annotations

import datetime as dt
from typing import Any

from config import Config
from services.http import UpstreamError, get_json, ttl_cache

BASE = "https://api.isthereanydeal.com"
GAME_PAGE = "https://isthereanydeal.com/game/{slug}/"
STEAM = 61  # ITAD's shop ID for Steam
SINCE = "2010-01-01T00:00:00Z"  # the whole log, not ITAD's default of three months
DAY = 86400
PACIFIC = dt.timedelta(hours=8)


def enabled() -> bool:
    return bool(Config.ITAD_API_KEY)


def steam_day(when: dt.datetime) -> dt.date:
    """The Steam day a moment belongs to.  Prices change at 10 a.m. Pacific
    (17:00 or 18:00 UTC); eight hours back puts any time from then until the
    next morning on the right date, whatever ITAD's detection lag."""
    if when.tzinfo is None:
        when = when.replace(tzinfo=dt.timezone.utc)
    return (when.astimezone(dt.timezone.utc) - PACIFIC).date()


@ttl_cache(ttl=7 * DAY, fail_ttl=300)
def game(appid: int) -> dict[str, Any] | None:
    """ITAD's ``{id, url}`` for a Steam app, or ``None`` if it doesn't know it."""
    data = get_json(f"{BASE}/games/lookup/v1", {"key": Config.ITAD_API_KEY, "appid": appid}) or {}
    found = data.get("game") or {}
    if not data.get("found") or not found.get("id"):
        return None
    slug = found.get("slug")
    return {"id": found["id"], "url": GAME_PAGE.format(slug=slug) if slug else None}


def parse_log(entries: Any) -> list[dict[str, Any]]:
    """ITAD's Steam price log as ``{day, price, regular}`` in US cents, oldest
    first.  ``price`` is ``None`` while Steam wasn't selling the game."""
    events = []
    for entry in entries if isinstance(entries, list) else []:
        try:
            when = dt.datetime.fromisoformat(entry["timestamp"])
        except (KeyError, TypeError, ValueError):
            continue
        deal = entry.get("deal")
        price = regular = None
        if deal:
            p, r = deal.get("price") or {}, deal.get("regular") or {}
            if p.get("currency", "USD") != "USD" or p.get("amountInt") is None:
                continue
            price = int(p["amountInt"])
            regular = int(r["amountInt"]) if r.get("amountInt") is not None else price
        events.append((when, {"day": steam_day(when), "price": price, "regular": regular}))
    events.sort(key=lambda e: e[0])
    return [e for _, e in events]


@ttl_cache(ttl=6 * 3600, fail_ttl=300)
def steam_history(appid: int) -> dict[str, Any] | None:
    """The game's whole US Steam price log: ``{events, url}``, or ``None`` if
    ITAD doesn't know the game.  Raises :class:`UpstreamError` when ITAD
    can't be reached or turns us away (including a missing key)."""
    if not enabled():
        raise UpstreamError("ITAD_API_KEY isn't set")
    found = game(appid)
    if not found:
        return None
    entries = get_json(
        f"{BASE}/games/history/v2",
        {"key": Config.ITAD_API_KEY, "id": found["id"], "country": "US", "shops": STEAM, "since": SINCE},
    )
    return {"events": parse_log(entries), "url": found["url"]}
