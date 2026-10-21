"""Keyless Steam endpoints (store + public Web API).

Functions return parsed data or ``None`` when Steam has a definite "nothing
here" answer, and raise :class:`UpstreamError` for transient failures.
Batched endpoints (``price_overview``, ``store_items``) cache per app ID, so
a list page and a game page share what they've already fetched.
"""

from __future__ import annotations

import json
import logging
from typing import Any, Iterable

from services.http import (
    UpstreamError,
    cached_batch,
    chunks,
    get_json,
    new_store,
    ttl_cache,
)

log = logging.getLogger(__name__)

STORE = "https://store.steampowered.com"
WEBAPI = "https://api.steampowered.com"
ASSET_BASE = "https://shared.akamai.steamstatic.com/store_item_assets/"

HOUR = 3600


# ══════════════════════════════════════════════════════════
#  Store: app details and prices
# ══════════════════════════════════════════════════════════

@ttl_cache(ttl=6 * HOUR, fail_ttl=60)
def appdetails(appid: int, cc: str = "us") -> dict[str, Any] | None:
    """Full store details for one app, or ``None`` if Steam doesn't know it."""
    data = get_json(f"{STORE}/api/appdetails", {"appids": appid, "cc": cc, "l": "english"})
    node = (data or {}).get(str(appid)) or {}
    details = node.get("data") if node.get("success") else None
    return details if isinstance(details, dict) else None


_price_store = new_store(maxsize=16384)


def price_overview(appids: Iterable[int], cc: str, *, failed: set[int] | None = None) -> dict[int, dict | None]:
    """``price_overview`` for many apps in one store, batched 50 per call.

    Values are ``None`` when the app isn't sold there (or is free), and also
    when Steam didn't answer; pass a set as *failed* to collect those app IDs.
    """
    ids = [int(a) for a in appids]

    def fetch(keys: list) -> dict:
        found: dict = {}
        for batch in chunks([k[0] for k in keys], 50):
            data = get_json(
                f"{STORE}/api/appdetails",
                {"appids": ",".join(map(str, batch)), "cc": cc, "filters": "price_overview"},
            )
            for appid in batch:
                node = (data or {}).get(str(appid)) or {}
                d = node.get("data")
                po = d.get("price_overview") if isinstance(d, dict) else None
                found[(appid, cc)] = po
        return found

    missed: set = set()
    res = cached_batch(_price_store, [(a, cc) for a in ids], fetch, ttl=3 * HOUR, failed=missed)
    if failed is not None:
        failed.update(k[0] for k in missed)
    return {k[0]: v for k, v in res.items()}


# ══════════════════════════════════════════════════════════
#  Store browse: batched item summaries (tags, reviews, art)
# ══════════════════════════════════════════════════════════

_ITEM_REQUEST = {
    "include_assets": True,
    "include_release": True,
    "include_platforms": True,
    "include_reviews": True,
    "include_basic_info": True,
    "include_tag_count": 20,
}

_item_store = new_store(maxsize=16384)


def store_items(appids: Iterable[int], cc: str = "US") -> dict[int, dict | None]:
    """Compact summaries for many apps (see :func:`_parse_item`).

    One Web API call per 50 apps.  Prices are for country *cc*.
    """
    cc = cc.upper()
    ids = [int(a) for a in appids]

    def fetch(keys: list) -> dict:
        found: dict = {}
        for batch in chunks([k[0] for k in keys], 50):
            payload = {
                "ids": [{"appid": a} for a in batch],
                "context": {"language": "english", "country_code": cc},
                "data_request": _ITEM_REQUEST,
            }
            data = get_json(
                f"{WEBAPI}/IStoreBrowseService/GetItems/v1/",
                {"input_json": json.dumps(payload, separators=(",", ":"))},
            )
            for item in ((data or {}).get("response") or {}).get("store_items") or []:
                if item.get("success") == 1 and item.get("appid"):
                    found[(int(item["appid"]), cc)] = _parse_item(item)
        return found

    res = cached_batch(_item_store, [(a, cc) for a in ids], fetch, ttl=6 * HOUR)
    return {k[0]: v for k, v in res.items()}


@ttl_cache(ttl=HOUR, fail_ttl=120)
def discount_end(appid: int, package_id: int | None = None) -> int | None:
    """When the game's current US discount ends (a Unix time), or ``None`` if
    it isn't discounted.

    Reads the purchase option for *package_id*, the game's main package
    (``appdetails["packages"][0]``), falling back to its first package: the
    best purchase option is sometimes a bundle with a deeper cut of its own.
    """
    payload = {
        "ids": [{"appid": appid}],
        "context": {"language": "english", "country_code": "US"},
        "data_request": {"include_all_purchase_options": True},
    }
    data = get_json(
        f"{WEBAPI}/IStoreBrowseService/GetItems/v1/",
        {"input_json": json.dumps(payload, separators=(",", ":"))},
    )
    items = ((data or {}).get("response") or {}).get("store_items") or []
    item = next((i for i in items if str(i.get("appid")) == str(appid)), None)
    packages = [o for o in (item or {}).get("purchase_options") or [] if o.get("packageid")]
    option = next((o for o in packages if str(o["packageid"]) == str(package_id)), packages[0] if packages else None)
    ends = [int(d["discount_end_date"]) for d in (option or {}).get("active_discounts") or []
            if d.get("discount_end_date")]
    return min(ends) if ends else None


def asset_url(item_assets: dict | None, key: str) -> str | None:
    """Build a CDN URL for one asset (``header``, ``library_hero``…)."""
    if not item_assets:
        return None
    fmt, filename = item_assets.get("asset_url_format"), item_assets.get(key)
    if not fmt or not filename:
        return None
    return ASSET_BASE + fmt.replace("${FILENAME}", filename)


def _parse_item(item: dict) -> dict[str, Any]:
    assets = item.get("assets") or {}
    reviews = (item.get("reviews") or {}).get("summary_filtered") or {}
    basic = item.get("basic_info") or {}
    platforms = item.get("platforms") or {}
    bpo = item.get("best_purchase_option") or {}

    price = None
    if bpo.get("final_price_in_cents") is not None:
        final = int(bpo["final_price_in_cents"])
        original = int(bpo.get("original_price_in_cents") or final)
        price = {
            "final": final / 100,
            "initial": original / 100,
            "discount": int(bpo.get("discount_pct") or 0),
            "final_formatted": bpo.get("formatted_final_price") or "",
            "initial_formatted": bpo.get("formatted_original_price") or "",
        }

    return {
        "appid": int(item["appid"]),
        "name": item.get("name") or "",
        "type": item.get("type"),
        "is_free": bool(item.get("is_free")),
        "is_coming_soon": bool(item.get("is_coming_soon")),
        "release_date": (item.get("release") or {}).get("steam_release_date"),
        "header": asset_url(assets, "header"),
        "capsule": asset_url(assets, "main_capsule"),
        "small_capsule": asset_url(assets, "small_capsule"),
        "hero": asset_url(assets, "library_hero"),
        "library_capsule": asset_url(assets, "library_capsule"),
        "tags": [(t["tagid"], t.get("weight", 0)) for t in item.get("tags") or []],
        "review_count": int(reviews.get("review_count") or 0),
        "review_pct": reviews.get("percent_positive"),
        "review_label": reviews.get("review_score_label") or "",
        "review_score": reviews.get("review_score"),
        "short_description": basic.get("short_description") or "",
        "developers": [d.get("name") for d in basic.get("developers") or [] if d.get("name")],
        "publishers": [p.get("name") for p in basic.get("publishers") or [] if p.get("name")],
        "platforms": {
            "windows": bool(platforms.get("windows")),
            "mac": bool(platforms.get("mac")),
            "linux": bool(platforms.get("steamos_linux")),
        },
        # 0 unknown, 1 unsupported, 2 playable, 3 verified
        "deck": int(platforms.get("steam_deck_compat_category") or 0),
        "price": price,
        "categories": item.get("categories") or {},
    }


@ttl_cache(ttl=24 * HOUR, fail_ttl=300)
def tag_names() -> dict[int, str]:
    """Steam's tag ID → English name map."""
    data = get_json(f"{WEBAPI}/IStoreService/GetTagList/v1/", {"language": "english"})
    tags = ((data or {}).get("response") or {}).get("tags") or []
    if not tags:
        raise UpstreamError("GetTagList returned no tags")
    return {int(t["tagid"]): t["name"] for t in tags}


@ttl_cache(ttl=30 * 60, fail_ttl=120)
def featured_categories(cc: str = "us") -> dict[str, Any]:
    """Store front lists: specials, top_sellers, new_releases, coming_soon."""
    return get_json(f"{STORE}/api/featuredcategories", {"cc": cc, "l": "english"}) or {}


@ttl_cache(ttl=6 * HOUR, fail_ttl=120)
def store_search(term: str, cc: str = "US") -> list[dict[str, Any]]:
    """Steam's own store search (used when our index has nothing good)."""
    data = get_json(f"{STORE}/api/storesearch/", {"term": term, "l": "english", "cc": cc})
    return (data or {}).get("items") or []


# ══════════════════════════════════════════════════════════
#  Reviews
# ══════════════════════════════════════════════════════════

@ttl_cache(ttl=2 * HOUR, fail_ttl=60)
def review_summary(appid: int) -> dict[str, Any]:
    data = get_json(
        f"{STORE}/appreviews/{appid}",
        {"json": 1, "filter": "summary", "language": "all", "purchase_type": "all", "num_per_page": 0},
    )
    return (data or {}).get("query_summary") or {}


@ttl_cache(ttl=2 * HOUR, fail_ttl=60)
def reviews_page(appid: int, filt: str = "recent") -> list[dict[str, Any]]:
    """One page (up to 100) of reviews; *filt* is ``recent`` or ``all``."""
    data = get_json(
        f"{STORE}/appreviews/{appid}",
        {"json": 1, "filter": filt, "language": "all", "purchase_type": "all", "num_per_page": 100},
    )
    return (data or {}).get("reviews") or []


@ttl_cache(ttl=12 * HOUR, fail_ttl=120)
def review_histogram(appid: int) -> dict[str, Any]:
    """Monthly up/down review counts (``rollups``) plus recent daily ones."""
    data = get_json(
        f"{STORE}/appreviewhistogram/{appid}",
        {"l": "english", "review_score_preference": 0},
    )
    return (data or {}).get("results") or {}


# ══════════════════════════════════════════════════════════
#  Players and charts
# ══════════════════════════════════════════════════════════

@ttl_cache(ttl=5 * 60, fail_ttl=60)
def current_players(appid: int) -> int | None:
    data = get_json(f"{WEBAPI}/ISteamUserStats/GetNumberOfCurrentPlayers/v1/", {"appid": appid})
    resp = (data or {}).get("response") or {}
    return int(resp["player_count"]) if resp.get("result") == 1 else None


@ttl_cache(ttl=10 * 60, fail_ttl=120)
def concurrent_chart() -> dict[str, Any]:
    """Top 100 by players right now: ``ranks`` of {rank, appid, concurrent_in_game, peak_in_game}."""
    data = get_json(f"{WEBAPI}/ISteamChartsService/GetGamesByConcurrentPlayers/v1/")
    return (data or {}).get("response") or {}


@ttl_cache(ttl=60 * 60, fail_ttl=120)
def most_played_chart() -> dict[str, Any]:
    """Daily peak chart: ``ranks`` of {rank, appid, last_week_rank, peak_in_game}."""
    data = get_json(f"{WEBAPI}/ISteamChartsService/GetMostPlayedGames/v1/")
    return (data or {}).get("response") or {}


@ttl_cache(ttl=60 * 60, fail_ttl=120)
def weekly_top_sellers(cc: str = "US", count: int = 50) -> dict[str, Any]:
    """Weekly best sellers: ``ranks`` of {rank, appid, last_week_rank, consecutive_weeks}."""
    payload = {
        "country_code": cc.upper(),
        "context": {"language": "english", "country_code": cc.upper()},
        "page_start": 0,
        "page_count": count,
    }
    data = get_json(
        f"{WEBAPI}/IStoreTopSellersService/GetWeeklyTopSellers/v1/",
        {"input_json": json.dumps(payload, separators=(",", ":"))},
    )
    return (data or {}).get("response") or {}
