"""Steam sales, ranked by a deal score you can read.

    deal score = 40 × discount (up to 90 % off)
               + 45 × reviews above 50 % positive
               + 15 × confidence from review count (100,000 reviews = full)

Games in the discovery pool also get their full value score at the sale
price, since we know their typical playtime.
"""

from __future__ import annotations

import math
from typing import Any

from services import cheapshark, pool, steam, tags, value_score
from services.http import UpstreamError, executor

DEAL_LABELS = [(75, "Great deal"), (60, "Good deal"), (45, "Fair deal")]


def deal_score(savings: float, rating_pct: float | None, rating_count: int) -> int:
    d = min(max(savings, 0.0), 90.0) / 90.0
    r = max(0.0, min(1.0, ((rating_pct or 0) - 50.0) / 50.0))
    c = min(1.0, math.log10(max(10, rating_count)) / 5.0)
    return int(round(100 * (0.40 * d + 0.45 * r + 0.15 * c)))


def deal_label(score: int) -> str:
    for floor, label in DEAL_LABELS:
        if score >= floor:
            return label
    return "So-so deal"


def _fetch_pages(pages: int) -> list[dict[str, Any]]:
    futures = [executor.submit(cheapshark.steam_deals, p) for p in range(pages)]
    out, seen = [], set()
    for f in futures:
        try:
            rows = f.result()
        except UpstreamError:
            continue
        for row in rows:
            if row["appid"] not in seen:
                seen.add(row["appid"])
                out.append(row)
    if not out:
        raise UpstreamError("no deals from CheapShark")
    return out


def current_deals(pages: int = 2) -> list[dict[str, Any]]:
    """Today's Steam sales (games only), best deal first."""
    raw = _fetch_pages(pages)
    items = steam.store_items([d["appid"] for d in raw])
    rows = []
    for d in raw:
        it = items.get(d["appid"])
        if it is not None and it["type"] != 0:
            continue  # DLC, soundtracks, software
        # Steam's own review numbers are fresher than CheapShark's copy.
        count = it["review_count"] if it else d["rating_count"]
        pct = (it["review_pct"] if it else d["rating_pct"]) if count >= 10 else None
        score = deal_score(d["savings"], pct, count)
        # Games in the discovery pool have measured playtime: give them a value score too.
        known = pool.get(d["appid"])
        value = None
        if known and pct is not None and pool.has_hours(known):
            v_score, v_label, _ = value_score.compute_value_score(d["sale_price"], pool.stats(known))
            value = {"score": v_score, "label": v_label}
        rows.append({
            "value": value,
            "appid": d["appid"],
            "name": (it or {}).get("name") or d["name"],
            "header": (it or {}).get("header") or d["thumb"],
            "capsule": (it or {}).get("small_capsule") or d["thumb"],
            "tags": tags.top_names((it or {}).get("tags") or [], 3),
            "sale_price": d["sale_price"],
            "normal_price": d["normal_price"],
            "savings": d["savings"],
            "review_pct": pct,
            "review_count": count,
            "review_label": ((it or {}).get("review_label") or d["rating_text"]) if pct is not None else "",
            "deal_score": score,
            "deal_label": deal_label(score),
            "url": d["url"],
            "release_date": d["release_date"],
        })
    rows.sort(key=lambda r: -r["deal_score"])
    return rows


SORTS = {
    "deal": ("Best deal", lambda r: -r["deal_score"]),
    "discount": ("Biggest discount", lambda r: (-r["savings"], -r["deal_score"])),
    "price": ("Lowest price", lambda r: (r["sale_price"], -r["deal_score"])),
    "reviews": ("Best reviewed", lambda r: (-(r["review_pct"] or 0), -r["review_count"])),
}


def filter_deals(
    rows: list[dict[str, Any]],
    *,
    max_price: float | None = None,
    min_rating: int | None = None,
    min_discount: int | None = None,
    sort: str = "deal",
) -> list[dict[str, Any]]:
    out = [
        r for r in rows
        if (max_price is None or r["sale_price"] <= max_price)
        and (min_rating is None or (r["review_pct"] or 0) >= min_rating)
        and (min_discount is None or r["savings"] >= min_discount)
    ]
    out.sort(key=SORTS.get(sort, SORTS["deal"])[1])
    return out
