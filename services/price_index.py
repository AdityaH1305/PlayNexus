"""Steam regional price index: where is Steam cheapest?

We price a basket of Steam's most popular paid games (this week's top sellers
and the most-played chart) in every region, convert to US dollars at today's
rates, and compare each game with its US price.  A region's index is the
median of those ratios, so one oddly priced game can't skew it.
"""

from __future__ import annotations

import statistics
import time
from typing import Any

from config import Config
from services import charts, fx, steam
from services.http import UpstreamError, executor, ttl_cache

AXIS_MAX = 1.5  # dots beyond 150 % of the US price sit at the edge


def _basket(limit: int) -> list[dict[str, Any]]:
    games: list[dict[str, Any]] = []
    seen: set[int] = set()
    for fn in (charts.top_sellers, charts.playing_now):
        try:
            rows = fn()["rows"]
        except UpstreamError:
            continue
        for row in rows:
            if row["is_free"] or not row["price"] or row["appid"] in seen:
                continue
            seen.add(row["appid"])
            games.append({"appid": row["appid"], "name": row["name"]})
    return games[:limit]


def _region_prices(ids: list[int], cc: str) -> dict[int, dict | None]:
    try:
        return steam.price_overview(ids, cc)
    except UpstreamError:
        return {}


@ttl_cache(ttl=6 * 3600, fail_ttl=300)
def compute(limit: int = 60) -> dict[str, Any]:
    games = _basket(limit)
    if len(games) < 10:
        raise UpstreamError("not enough games to build the index")
    ids = [g["appid"] for g in games]
    names = {g["appid"]: g["name"] for g in games}

    futures = {r["cc"]: executor.submit(_region_prices, ids, r["cc"]) for r in Config.REGIONS}
    prices = {cc: f.result() for cc, f in futures.items()}
    us = prices["us"]

    rows, gaps = [], []
    for region in Config.REGIONS:
        if region["cc"] == "us":
            continue
        ratios = []
        currency = None
        for appid in ids:
            po_us, po = us.get(appid), prices[region["cc"]].get(appid)
            if not po_us or not po or not po_us.get("final"):
                continue
            usd = fx.to_usd(po["final"] / 100, po["currency"])
            if usd is None:
                continue
            currency = po["currency"]
            ratio = usd / (po_us["final"] / 100)
            ratios.append(ratio)
            gaps.append({"appid": appid, "name": names[appid], "code": region["code"],
                         "region": region["name"], "pct": round(ratio * 100)})
        if len(ratios) < 5:
            continue
        q1, median, q3 = statistics.quantiles(ratios, n=4) if len(ratios) >= 4 else (min(ratios), statistics.median(ratios), max(ratios))
        rows.append({
            **region,
            "currency": currency,
            "count": len(ratios),
            "median_pct": round(median * 100),
            "q1_x": round(min(q1, AXIS_MAX) / AXIS_MAX * 100, 2),
            "q3_x": round(min(q3, AXIS_MAX) / AXIS_MAX * 100, 2),
            "median_x": round(min(median, AXIS_MAX) / AXIS_MAX * 100, 2),
            "dots": sorted(round(min(r, AXIS_MAX) / AXIS_MAX * 100, 2) for r in ratios),
        })
    rows.sort(key=lambda r: r["median_pct"])
    gaps.sort(key=lambda g: g["pct"])
    return {
        "rows": rows,
        "basket": len(games),
        "gaps": gaps[:10],
        "us_x": round(1 / AXIS_MAX * 100, 2),
        "updated": int(time.time()),
        "fx": fx.meta(),
    }
