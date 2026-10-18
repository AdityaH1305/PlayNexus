"""Small formatting helpers shared by templates and the API."""

from __future__ import annotations

import datetime as dt

LEGACY_CDN = "https://cdn.cloudflare.steamstatic.com/steam/apps/{appid}/{file}"
ART_FILES = {
    "header": "header.jpg",
    "capsule": "capsule_231x87.jpg",
    "hero": "library_hero.jpg",
    "library": "library_600x900.jpg",
}


def art_url(appid: int, kind: str = "header") -> str:
    """Steam's legacy art path. Newer games 404 here; the front end then
    falls back to /api/art/<appid>, which knows the real (hashed) URL."""
    return LEGACY_CDN.format(appid=appid, file=ART_FILES.get(kind, ART_FILES["header"]))


def comma(n) -> str:
    return "—" if n is None else f"{int(round(n)):,}"


def compact(n) -> str:
    """12,345 → 12.3K; 1,234,567 → 1.2M."""
    if n is None:
        return "—"
    n = float(n)
    for div, suffix in ((1e9, "B"), (1e6, "M"), (1e3, "K")):
        if abs(n) >= div:
            s = f"{n / div:.1f}".rstrip("0").rstrip(".")
            return f"{s}{suffix}"
    return f"{int(n)}"


def usd(n) -> str:
    return "—" if n is None else f"${n:,.2f}"


def date(ts, style: str = "month") -> str:
    """Unix timestamp (UTC) → "Oct 2026" (month), "6 Oct 2026" (day) or
    "6 Oct 2026, 09:30 UTC" (time)."""
    if not ts:
        return ""
    d = dt.datetime.fromtimestamp(int(ts), tz=dt.timezone.utc)
    if style == "day":
        return f"{d.day} {d:%b %Y}"
    if style == "time":
        return f"{d.day} {d:%b %Y, %H:%M} UTC"
    return f"{d:%b %Y}"


def hours(h) -> str:
    if h is None:
        return "—"
    if h < 1:
        return f"{round(h * 60)} min"
    if h < 10:
        return f"{h:.1f} h"
    return f"{round(h):,} h"
