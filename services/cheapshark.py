"""CheapShark: prices for the same game at other stores, and its all-time low.

CheapShark tracks official PC stores (Steam, GOG, Humble, Fanatical, …) in
US dollars.  It asks API users to send a descriptive User-Agent (we do, via
services.http) and to link deals through its redirect URL.
"""

from __future__ import annotations

from typing import Any

from services.http import get_json, ttl_cache

BASE = "https://www.cheapshark.com/api/1.0"
REDIRECT = "https://www.cheapshark.com/redirect?dealID={deal}"
ICON = "https://www.cheapshark.com{path}"
HOUR = 3600


@ttl_cache(ttl=24 * HOUR, fail_ttl=600)
def stores() -> dict[str, dict[str, str]]:
    """storeID → {name, icon}, active stores only."""
    data = get_json(f"{BASE}/stores") or []
    return {
        s["storeID"]: {"name": s["storeName"], "icon": ICON.format(path=(s.get("images") or {}).get("icon", ""))}
        for s in data
        if s.get("isActive")
    }


@ttl_cache(ttl=6 * HOUR, fail_ttl=300)
def game_deals(appid: int) -> dict[str, Any] | None:
    """All-time low and current deals for a Steam app, or ``None`` if unknown.

    ``{"cheapest_ever": {"price", "date"} | None, "deals": [...]}`` with deals
    sorted cheapest first; each has ``store``, ``price``, ``retail``,
    ``savings`` (percent) and ``url``.
    """
    matches = get_json(f"{BASE}/games", {"steamAppID": appid}) or []
    match = next((m for m in matches if str(m.get("steamAppID")) == str(appid)), None)
    if not match:
        return None
    detail = get_json(f"{BASE}/games", {"id": match["gameID"]}) or {}
    names = stores()

    deals = []
    for d in detail.get("deals") or []:
        store = names.get(d.get("storeID"))
        if not store:
            continue
        deals.append({
            "store": store["name"],
            "store_id": d["storeID"],
            "icon": store["icon"],
            "price": float(d["price"]),
            "retail": float(d["retailPrice"]),
            "savings": round(float(d.get("savings") or 0)),
            "url": REDIRECT.format(deal=d["dealID"]),
        })
    deals.sort(key=lambda d: d["price"])

    ever = detail.get("cheapestPriceEver") or {}
    return {
        "cheapest_ever": {"price": float(ever["price"]), "date": ever.get("date")} if ever.get("price") else None,
        "deals": deals,
    }


@ttl_cache(ttl=HOUR, fail_ttl=300)
def steam_deals(page: int = 0, page_size: int = 60, sort: str = "Deal Rating") -> list[dict[str, Any]]:
    """One page of current Steam sales from CheapShark's deal feed."""
    data = get_json(
        f"{BASE}/deals",
        {"storeID": 1, "onSale": 1, "pageNumber": page, "pageSize": page_size, "sortBy": sort},
    ) or []
    out = []
    for d in data:
        if not d.get("steamAppID"):
            continue
        out.append({
            "appid": int(d["steamAppID"]),
            "name": d.get("title") or "",
            "sale_price": float(d["salePrice"]),
            "normal_price": float(d["normalPrice"]),
            "savings": round(float(d.get("savings") or 0)),
            "rating_pct": int(d["steamRatingPercent"]) if d.get("steamRatingPercent") not in (None, "0") else None,
            "rating_count": int(d.get("steamRatingCount") or 0),
            "rating_text": d.get("steamRatingText") or "",
            "metacritic": int(d["metacriticScore"]) if d.get("metacriticScore") not in (None, "0") else None,
            "release_date": d.get("releaseDate"),
            "deal_rating": float(d.get("dealRating") or 0),
            "thumb": d.get("thumb"),
            "url": REDIRECT.format(deal=d["dealID"]),
        })
    return out
