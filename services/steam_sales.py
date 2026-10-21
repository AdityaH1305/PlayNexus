"""Steam's four seasonal sales (Spring, Summer, Autumn, Winter), past and announced.

Every game on Steam is invited to them, and they're exempt from Steam's
30 days between discounts, so they're when most games are at their cheapest.
The dates live in data/steam_sales.json; refresh it once a year from Valve's
"Upcoming Steam Events" page (the file lists its sources).

Dates are Steam's days: a sale runs from 10 a.m. Pacific on ``start`` to
10 a.m. Pacific on ``end``, so its full days are ``start`` up to, not
including, ``end``.
"""

from __future__ import annotations

import datetime as dt
import json
import os
from functools import lru_cache
from typing import Any

from config import Config

PATH = os.path.join(Config.DATA_DIR, "steam_sales.json")


@lru_cache(maxsize=1)
def sales() -> tuple[dict[str, Any], ...]:
    """Every seasonal sale in the file, oldest first: ``{name, start, end}``."""
    with open(PATH, encoding="utf-8") as f:
        raw = json.load(f)["sales"]
    parsed = (
        {"name": s["name"], "start": dt.date.fromisoformat(s["start"]), "end": dt.date.fromisoformat(s["end"])}
        for s in raw
    )
    return tuple(sorted(parsed, key=lambda s: s["start"]))


def label(sale: dict[str, Any]) -> str:
    """"Winter Sale 2026": named for the year it starts."""
    return f"{sale['name']} {sale['start'].year}"


def running(today: dt.date) -> dict[str, Any] | None:
    """The sale on *today*, if there is one."""
    return next((s for s in sales() if s["start"] <= today < s["end"]), None)


def upcoming(today: dt.date, eligible_from: dt.date | None = None) -> dict[str, Any] | None:
    """The next sale to start after *today* that a game able to take discounts
    from *eligible_from* can join; a cooldown that ends partway through a sale
    still lets it join."""
    return next(
        (s for s in sales() if s["start"] > today and (eligible_from is None or eligible_from < s["end"])),
        None,
    )


def past(today: dt.date) -> list[dict[str, Any]]:
    """Sales that are over by *today*, most recent first."""
    return [s for s in reversed(sales()) if s["end"] <= today]


def between(start: dt.date, end: dt.date) -> list[dict[str, Any]]:
    """Sales that overlap ``start``…``end``, oldest first."""
    return [s for s in sales() if s["start"] < end and s["end"] > start]
