"""Regional pricing.

One ``price_overview`` call per store region (run in parallel), converted to
US dollars with live exchange rates.  Display strings are Steam's own
(``final_formatted``), so every currency is formatted the way its store does.
List pages use :func:`local_prices` instead: many apps, one region.
"""

from __future__ import annotations

import statistics
from concurrent.futures import Future
from typing import Any

from config import Config
from services import fx, steam
from services.http import UpstreamError, chunks, executor

_ERROR = object()


def _price_fields(po: dict[str, Any]) -> dict[str, Any]:
    """Steam's ``price_overview`` in our units, with the US-dollar equivalent."""
    final = po["final"] / 100
    usd = fx.to_usd(final, po["currency"])
    return {
        "currency": po["currency"],
        "final": final,
        "initial": po["initial"] / 100,
        "discount": int(po.get("discount_percent") or 0),
        "final_formatted": po.get("final_formatted") or f"{final:,.2f} {po['currency']}",
        "initial_formatted": po.get("initial_formatted") or "",
        "usd": round(usd, 2) if usd is not None else None,
    }


def local_prices(appids: list[int], cc: str) -> tuple[dict[int, dict | None], set[int]]:
    """One store's prices for many apps, 50 per Steam call, run in parallel.

    Returns ``(prices, failed)``: *prices* maps each app to its price fields,
    or to ``None`` if that store doesn't sell it (or it's free).  Apps Steam
    didn't answer for are left out of *prices* and listed in *failed*.
    """

    def batch(ids: list[int]) -> tuple[dict[int, dict | None], set[int]]:
        missed: set[int] = set()
        return steam.price_overview(ids, cc, failed=missed), missed

    prices: dict[int, dict | None] = {}
    failed: set[int] = set()
    for future in [executor.submit(batch, ids) for ids in chunks(list(dict.fromkeys(appids)), 50)]:
        found, missed = future.result()
        failed |= missed
        prices.update({a: _price_fields(po) if po else None for a, po in found.items() if a not in missed})
    return prices, failed


def _region_price(appid: int, cc: str) -> Any:
    """Steam's price in one store: ``None`` if it isn't sold there, ``_ERROR`` if Steam didn't answer."""
    missed: set[int] = set()
    try:
        po = steam.price_overview([appid], cc, failed=missed).get(appid)
    except UpstreamError:
        return _ERROR
    return _ERROR if appid in missed else po


def submit_regional(appid: int) -> dict[str, Future]:
    """Start one price lookup per region; pass the result to :func:`assemble_regional`.

    Split in two so callers can overlap these requests with others without a
    pool thread ever blocking on another pool task.
    """
    return {r["cc"]: executor.submit(_region_price, appid, r["cc"]) for r in Config.REGIONS}


def regional_prices(appid: int, *, is_free: bool = False) -> dict[str, Any]:
    """Prices for *appid* in every configured region (see :func:`assemble_regional`)."""
    return assemble_regional(None if is_free else submit_regional(appid), is_free=is_free)


def assemble_regional(futures: dict[str, Future] | None, *, is_free: bool = False) -> dict[str, Any]:
    """Collect regional prices.

    Returns ``regions`` (one row per region, in config order), ``us_usd``,
    ``reference_usd`` (US price, or the median when there's no US price),
    ``cheapest`` / ``priciest`` region codes, and exchange-rate metadata.
    """
    if is_free or futures is None:
        return {
            "is_free": True, "available": True, "regions": [], "us_usd": 0.0,
            "reference_usd": 0.0, "cheapest": None, "priciest": None, "fx": fx.meta(),
        }

    rows: list[dict[str, Any]] = []
    for region in Config.REGIONS:
        po = futures[region["cc"]].result()
        row: dict[str, Any] = {**region, "available": False, "error": po is _ERROR}
        if po and po is not _ERROR:
            row.update(
                available=True,
                **_price_fields(po),
                usd_priced=po["currency"] == "USD" and region["cc"] != "us",
            )
        rows.append(row)

    priced = [r for r in rows if r.get("usd")]
    us_usd = next((r["usd"] for r in priced if r["cc"] == "us"), None)
    reference = us_usd if us_usd else (statistics.median(r["usd"] for r in priced) if priced else None)

    for r in priced:
        r["vs_us_pct"] = round((r["usd"] - us_usd) / us_usd * 100, 1) if us_usd else None

    cheapest = min(priced, key=lambda r: r["usd"]) if len(priced) > 1 else None
    priciest = max(priced, key=lambda r: r["usd"]) if len(priced) > 1 else None
    for r in rows:
        r["is_cheapest"] = cheapest is not None and r is cheapest
        r["is_priciest"] = priciest is not None and r is priciest

    return {
        "is_free": False,
        "available": bool(priced),
        "regions": rows,
        "us_usd": us_usd,
        "reference_usd": reference,
        "cheapest": cheapest["code"] if cheapest else None,
        "priciest": priciest["code"] if priciest else None,
        "fx": fx.meta(),
    }
