"""Steam profile tools: wishlist sales, library stats, backlog.

Nothing here is stored.  Profile lookups and wishlists work without an API
key; the library needs ``STEAM_API_KEY`` and a profile whose game details
are public.
"""

from __future__ import annotations

import math
import re
import xml.etree.ElementTree as ET
from typing import Any

from config import Config
from services import pool, steam, value_score
from services.http import UpstreamError, chunks, executor, get_json, get_text, ttl_cache

COMMUNITY = "https://steamcommunity.com"
WEBAPI = "https://api.steampowered.com"
STEAMID_RE = re.compile(r"^7656119\d{10}$")
VANITY_RE = re.compile(r"^[A-Za-z0-9_-]{2,32}$")
URL_RE = re.compile(r"steamcommunity\.com/(id|profiles)/([^/?#\s]+)", re.IGNORECASE)


class ProfileError(Exception):
    """Something the visitor can fix: a bad ID, a private profile…"""


def has_key() -> bool:
    return bool(Config.STEAM_API_KEY)


def parse_input(text: str) -> tuple[str, str]:
    """``("steamid", "7656…")`` or ``("vanity", "name")`` from what someone typed."""
    text = (text or "").strip()
    m = URL_RE.search(text)
    if m:
        kind = "vanity" if m.group(1).lower() == "id" else "steamid"
        value = m.group(2)
    elif STEAMID_RE.match(text):
        kind, value = "steamid", text
    elif VANITY_RE.match(text):
        kind, value = "vanity", text
    else:
        raise ProfileError("That doesn't look like a Steam profile. Paste your profile link or your 17-digit SteamID.")
    if kind == "steamid" and not STEAMID_RE.match(value):
        raise ProfileError("That SteamID isn't valid. It should be 17 digits starting with 7656.")
    return kind, value


@ttl_cache(ttl=3600, fail_ttl=60)
def _profile_xml(kind: str, value: str) -> dict[str, Any] | None:
    path = "id" if kind == "vanity" else "profiles"
    body = get_text(f"{COMMUNITY}/{path}/{value}", {"xml": 1})
    if "<!DOCTYPE" in body[:200] or "<!ENTITY" in body:
        return None  # an HTML error page, or XML we won't parse
    try:
        root = ET.fromstring(body.encode("utf-8"))
    except ET.ParseError:
        return None
    steamid = (root.findtext("steamID64") or "").strip()
    if not STEAMID_RE.match(steamid):
        return None
    return {
        "steamid": steamid,
        "name": (root.findtext("steamID") or "").strip(),
        "avatar": (root.findtext("avatarMedium") or "").strip() or None,
        "public": (root.findtext("privacyState") or "").strip() == "public",
    }


def resolve(text: str) -> dict[str, Any]:
    """Find the profile behind a link, SteamID or custom URL name."""
    kind, value = parse_input(text)
    profile = _profile_xml(kind, value)
    if not profile:
        raise ProfileError("We couldn't find that Steam profile. Check the link or ID.")
    return profile


@ttl_cache(ttl=600, fail_ttl=60)
def wishlist_items(steamid: str) -> list[dict[str, Any]]:
    data = get_json(f"{WEBAPI}/IWishlistService/GetWishlist/v1/", {"steamid": steamid})
    items = ((data or {}).get("response") or {}).get("items") or []
    return [
        {"appid": int(i["appid"]), "priority": i.get("priority", 0), "added": i.get("date_added")}
        for i in items
        if i.get("appid")
    ]


@ttl_cache(ttl=600, fail_ttl=60)
def owned_games(steamid: str) -> list[dict[str, Any]] | None:
    """Owned games, or ``None`` if the profile's game details are private."""
    if not has_key():
        raise ProfileError("Library tools need the site's Steam API key, which isn't set up.")
    data = get_json(
        f"{WEBAPI}/IPlayerService/GetOwnedGames/v1/",
        {"key": Config.STEAM_API_KEY, "steamid": steamid, "include_appinfo": 1, "include_played_free_games": 1},
    )
    resp = (data or {}).get("response") or {}
    if "games" not in resp:
        return None
    return [
        {
            "appid": int(g["appid"]),
            "name": g.get("name") or f"App {g['appid']}",
            "minutes": int(g.get("playtime_forever") or 0),
            "last_played": g.get("rtime_last_played") or None,
        }
        for g in resp["games"]
    ]


def _value_for(appid: int, price: float | None, is_free: bool) -> dict[str, Any] | None:
    g = pool.get(appid)
    if not g or not pool.has_hours(g) or (not is_free and not price):
        return None
    score, label, _ = value_score.compute_value_score(0.0 if is_free else price, pool.stats(g), is_free=is_free)
    return {"score": score, "label": label}


def wishlist(steamid: str, cc: str = "US") -> dict[str, Any]:
    items = wishlist_items(steamid)
    if not items:
        return {"count": 0, "on_sale": 0, "games": []}
    ids = [i["appid"] for i in items][:400]
    store = steam.store_items(ids, cc)
    games = []
    for i in items[:400]:
        s = store.get(i["appid"])
        if not s or not s["name"]:
            continue
        price = s["price"]
        reliable = s["review_count"] >= 10
        games.append({
            "appid": s["appid"],
            "name": s["name"],
            "capsule": s["small_capsule"],
            "header": s["header"],
            "is_free": s["is_free"],
            "coming_soon": s["is_coming_soon"],
            "price": price,
            "discount": (price or {}).get("discount", 0),
            "review_pct": s["review_pct"] if reliable else None,
            "review_label": s["review_label"] if reliable else "",
            "value": _value_for(s["appid"], (price or {}).get("final") if cc == "US" else None, s["is_free"]),
            "priority": i["priority"],
            "added": i["added"],
        })
    # On sale first (biggest, best-reviewed discounts), then by your own order.
    games.sort(key=lambda g: (
        0 if g["discount"] else 1,
        -(g["discount"] * ((g["review_pct"] or 50) / 100)),
        g["priority"] or 9999,
    ))
    return {"count": len(games), "on_sale": sum(1 for g in games if g["discount"]), "games": games}


def _library_prices(ids: list[int]) -> dict[int, dict | None]:
    futures = [executor.submit(steam.price_overview, batch, "us") for batch in chunks(ids, 50)]
    out: dict[int, dict | None] = {}
    for f in futures:
        try:
            out.update(f.result())
        except UpstreamError:
            continue
    return out


def library(steamid: str) -> dict[str, Any] | None:
    owned = owned_games(steamid)
    if owned is None:
        return None
    owned.sort(key=lambda g: -g["minutes"])
    total_minutes = sum(g["minutes"] for g in owned)
    never = [g for g in owned if g["minutes"] == 0]

    ids = [g["appid"] for g in owned][:1500]
    prices = _library_prices(ids)
    current_value = sum((p or {}).get("final", 0) for p in prices.values() if p and p.get("currency") == "USD") / 100

    # Backlog: unplayed games, with review scores so the picker can favour good ones.
    backlog_ids = [g["appid"] for g in never][:250]
    meta = steam.store_items(backlog_ids) if backlog_ids else {}
    backlog = []
    for g in never[:250]:
        s = meta.get(g["appid"])
        if not s or s["type"] != 0:
            continue
        backlog.append({
            "appid": g["appid"],
            "name": s["name"] or g["name"],
            "header": s["header"],
            "review_pct": s["review_pct"] if s["review_count"] >= 10 else None,
            "hours": round(pool.effective_hours(pool.get(g["appid"])), 1) if pool.get(g["appid"]) else None,
        })

    return {
        "count": len(owned),
        "hours": round(total_minutes / 60),
        "never_played": len(never),
        "current_value_usd": round(current_value, 2),
        "priced_count": sum(1 for p in prices.values() if p),
        "most_played": [
            {"appid": g["appid"], "name": g["name"], "hours": round(g["minutes"] / 60, 1)}
            for g in owned[:10] if g["minutes"]
        ],
        "backlog": backlog,
        "suggestions": suggestions(owned),
    }


def suggestions(owned: list[dict[str, Any]], limit: int = 8) -> list[dict[str, Any]]:
    """Games you don't own that share tags with what you play most."""
    owned_ids = {g["appid"] for g in owned}
    profile: dict[int, float] = {}
    for g in owned[:20]:
        pg = pool.get(g["appid"])
        if not pg or not g["minutes"]:
            continue
        weight = math.log10(g["minutes"] / 60 + 1)
        for tid, w in pg["vec"].items():
            profile[tid] = profile.get(tid, 0.0) + w * weight
    if not profile:
        return []
    norm = math.sqrt(sum(w * w for w in profile.values())) or 1.0
    scored = []
    for g in pool.games():
        if g["appid"] in owned_ids:
            continue
        shared = profile.keys() & g["vec"].keys()
        if not shared:
            continue
        sim = sum(profile[t] * g["vec"][t] for t in shared) / (norm * g["norm"])
        quality = (g["review_pct"] or 0) / 100
        scored.append((sim * (0.6 + 0.4 * quality), g, shared))
    scored.sort(key=lambda t: -t[0])
    return [
        {
            "appid": g["appid"],
            "name": g["name"],
            "header": g["header"],
            "review_pct": g["review_pct"],
            "because": ", ".join(n for n in (pool.tag_name(t) for t in sorted(shared, key=lambda t: -profile[t] * g["vec"][t])[:3]) if n),
        }
        for _, g, shared in scored[:limit]
    ]
