"""Assemble everything we know about one game.

The page route and the JSON API both call :func:`load_game`, so they can't
drift apart.  Every upstream request is started before any is awaited, and
only leaf tasks (ones that never wait on other pool tasks) go to the pool.
"""

from __future__ import annotations

import html
import logging
import re
from datetime import date, datetime
from typing import Any

from services import pool, pricing, reviews, steam, tags, value_score, vibes
from services.http import UpstreamError, executor

log = logging.getLogger(__name__)

STORE_URL = "https://store.steampowered.com/app/{appid}"
DECK_LABELS = {3: "Verified", 2: "Playable", 1: "Unsupported"}
CONTROLLER = {"full": "Full support", "partial": "Partial support"}
IN_APP_PURCHASES = 35  # Steam category ID
EARLY_ACCESS = "70"  # Steam genre ID
NOTICE_MAX = 200  # characters of Steam's account and DRM notices


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
        "controller": CONTROLLER.get(details.get("controller_support") or ""),
    }


def _plain(text: str | None) -> str:
    """Steam's notice text without markup, on one line, cut to a sensible length."""
    text = " ".join(re.sub(r"<[^>]*>", " ", html.unescape(text or "")).split())
    return text if len(text) <= NOTICE_MAX else text[: NOTICE_MAX - 1].rstrip() + "…"


def _years(months: int) -> str:
    """Roughly how long, for a sentence: "a year", "over 2 years", "almost 3 years"."""
    years, rest = divmod(months, 12)
    prefix = ""
    if rest >= 9:
        years, prefix = years + 1, "almost "
    elif rest >= 3:
        prefix = "over "
    return prefix + ("a year" if years == 1 else f"{years} years")


def buyer_notes(
    details: dict[str, Any], *, trend: dict[str, Any] | None, is_free: bool, release: str,
    coming_soon: bool, store_url: str, today: date | None = None,
) -> list[dict[str, Any]]:
    """What to know before buying, mostly in Steam's own words:
    ``[{kind, tone, icon, title, detail, href, link}]``, most pressing first.
    Most games have none."""
    notes: list[dict[str, Any]] = []

    def note(kind, tone, icon, title, detail="", href=None, link=None):
        notes.append({"kind": kind, "tone": tone, "icon": icon, "title": title,
                      "detail": detail, "href": href, "link": link})

    if trend:
        worse = trend["direction"] == "worse"
        note("trend", "neg" if worse else "pos", "down" if worse else "up",
             f"Recent reviews are much more {'negative' if worse else 'positive'}",
             f"{trend['recent_pct']}% positive in the last 30 days ({trend['recent_count']:,} reviews), "
             f"against {trend['overall_pct']}% overall.",
             f"{store_url}#app_reviews_hash", "Read recent reviews on Steam")

    if any(str(g.get("id")) == EARLY_ACCESS for g in details.get("genres") or []):
        unfinished = "It isn't finished, and may or may not change further."
        started = None if coming_soon else release_iso(release)
        if coming_soon:
            note("early_access", "caution", "clock", "Launches in Early Access", unfinished)
        elif started:
            start, today = date.fromisoformat(started), today or date.today()
            months = (today.year - start.year) * 12 + today.month - start.month - (today.day < start.day)
            if months >= 12:
                note("early_access", "caution", "clock", f"In Early Access for {_years(months)}",
                     f"Since {release}. {unfinished}")
            else:
                note("early_access", "caution", "clock", f"In Early Access since {release}", unfinished)
        else:
            note("early_access", "caution", "clock", "In Early Access", unfinished)

    if account := _plain(details.get("ext_user_account_notice")):
        note("account", "caution", "link", "Needs a third-party account", account)
    if drm := _plain(details.get("drm_notice")):
        note("drm", "caution", "lock", "Third-party DRM", drm)
    if any(c.get("id") == IN_APP_PURCHASES for c in details.get("categories") or []):
        note("iap", "caution", "coins",
             "Free to play, with in-app purchases" if is_free else "In-app purchases on top of the price")

    demo = next((d["appid"] for d in details.get("demos") or [] if str(d.get("appid", "")).isdigit()), None)
    if demo:
        note("demo", "pos", "gamepad", "Free demo on Steam", "Try it before you buy.",
             STORE_URL.format(appid=int(demo)), "Get the demo")
    return notes


def load_game(appid: int) -> dict[str, Any] | None:
    """Return the game bundle, or ``None`` if Steam has no such app.

    Raises :class:`UpstreamError` if Steam can't be reached at all.
    """
    details = steam.appdetails(appid, "us")
    if not details:
        return None

    is_free = bool(details.get("is_free"))
    meta = _meta(details)
    is_dlc = details.get("type") == "dlc"
    fullgame = details.get("fullgame") or {}
    base_game = (
        {"appid": int(fullgame["appid"]), "name": fullgame.get("name") or "the base game"}
        if is_dlc and str(fullgame.get("appid", "")).isdigit()
        else None
    )
    item_future = executor.submit(_store_item, appid)
    price_futures = None if is_free else pricing.submit_regional(appid)
    review_futures = reviews.submit_review_calls(appid)

    stats = reviews.assemble_review_stats(review_futures)
    prices = pricing.assemble_regional(price_futures, is_free=is_free)
    item = item_future.result()

    # Steam's review endpoints are the first to rate-limit. Fall back to the
    # review numbers in the store item and the pool's measured playtime, and
    # never mistake a failed lookup for "no reviews".
    degraded = (not stats["available"]) or item is None or any(r.get("error") for r in prices["regions"])
    if not stats["available"] and item and item["review_count"]:
        stats.update(
            available=True,
            positivity=(item["review_pct"] or 0) / 100,
            total_reviews=item["review_count"],
            review_label=item["review_label"],
        )
    known = pool.get(appid)
    if known and pool.has_hours(known) and not (stats["p50_all"] or stats["p50_recent"]):
        stats.update({k: v for k, v in known["hours"].items()})
    enough_data = stats["available"] and (is_free or stats["p50_all"] or stats["p50_recent"] or stats["p75_recent"])

    try:
        scores = vibes.score_tags(item["tags"]) if item and item["tags"] else {}
        vibe_info = {"scores": {k: v["score"] for k, v in scores.items()}, "top": vibes.top_vibes(scores)}
    except Exception:  # vibes are a nice-to-have; never fail the page for them
        log.exception("vibe scoring failed for %s", appid)
        vibe_info = {"scores": {}, "top": []}

    usd = 0.0 if is_free else prices["reference_usd"]
    score, label, breakdown = value_score.compute_value_score(usd, stats, is_free=is_free)
    if not enough_data and score is not None:
        score, label = None, "Not enough data"
    if is_dlc:
        # DLC reviewers' playtime is their base-game playtime, which would
        # flatter the DLC; value scores are for full games.
        score, label = None, "DLC"

    worth_it = None
    if score is not None and not is_free and stats["total_reviews"]:
        worth_it = {
            "great": value_score.worth_it_price(stats, 70),
            "good": value_score.worth_it_price(stats, 55),
        }

    by_region = {}
    for row in prices["regions"] if score is not None else []:
        if row.get("usd"):
            s, lbl, b = value_score.compute_value_score(row["usd"], stats)
            by_region[row["code"]] = {"score": s, "label": lbl, "cost_per_hour": b["cost_per_hour"]}

    header = details.get("header_image") or (item or {}).get("header")
    store_url = STORE_URL.format(appid=appid)
    notes = buyer_notes(
        details, trend=stats.get("trend"), is_free=is_free, release=meta["release"],
        coming_soon=meta["coming_soon"], store_url=store_url,
    )
    return {
        "appid": appid,
        "name": details.get("name") or (item or {}).get("name") or f"App {appid}",
        "degraded": degraded,
        "is_dlc": is_dlc,
        "base_game": base_game,
        "details": details,
        "meta": meta,
        "is_free": is_free,
        "coming_soon": meta["coming_soon"],
        "header_image": header,
        "hero_image": (item or {}).get("hero") or details.get("background_raw") or header,
        "store_url": store_url,
        "notes": notes,
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


# Steam writes release dates for people, day or month first.
RELEASE_FORMATS = ("%b %d, %Y", "%d %b, %Y", "%B %d, %Y", "%d %B, %Y")


def release_iso(text: str) -> str | None:
    """Steam's "Aug 3, 2023" or "3 Aug, 2023" as "2023-08-03"; ``None`` for
    anything vaguer, like "Q1 2027" or "Coming soon"."""
    for fmt in RELEASE_FORMATS:
        try:
            return datetime.strptime(text.strip(), fmt).date().isoformat()
        except ValueError:
            pass
    return None


def structured_data(game: dict[str, Any], url: str) -> dict[str, Any]:
    """schema.org ``VideoGame`` JSON-LD for the game page.

    Deliberately no ``aggregateRating``: the reviews are Steam's, and Google
    doesn't allow third-party ratings marked up as the page's own.
    """
    meta = game["meta"]
    optional = {
        "image": game["header_image"],
        "genre": meta["genres"],
        "gamePlatform": meta["platforms"],
        "publisher": [{"@type": "Organization", "name": name} for name in meta["publishers"]],
        "datePublished": None if meta["coming_soon"] else release_iso(meta["release"]),
    }
    return {
        "@context": "https://schema.org",
        "@type": "VideoGame",
        "name": game["name"],
        "url": url,
        **{key: value for key, value in optional.items() if value},
    }
