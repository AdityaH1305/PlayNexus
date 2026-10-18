"""Currency conversion to US dollars using daily mid-market rates.

Rates come from ExchangeRate-API's free endpoint (no key, updated daily).
If it's unreachable we fall back to the snapshot in data/fx_fallback.json,
which scripts/build_data.py refreshes.
"""

from __future__ import annotations

import json
import logging
import os
from functools import lru_cache
from typing import Any

from config import Config
from services.http import UpstreamError, get_json, ttl_cache

log = logging.getLogger(__name__)

FX_URL = "https://open.er-api.com/v6/latest/USD"
FALLBACK_PATH = os.path.join(Config.DATA_DIR, "fx_fallback.json")


@ttl_cache(ttl=12 * 3600, fail_ttl=600)
def _live_rates() -> dict[str, Any]:
    data = get_json(FX_URL)
    if not data or data.get("result") != "success" or not data.get("rates"):
        raise UpstreamError("exchange rates unavailable")
    return {
        "rates": data["rates"],
        "updated": data.get("time_last_update_unix"),
        "source": "ExchangeRate-API",
        "live": True,
    }


@lru_cache(maxsize=1)
def _fallback_rates() -> dict[str, Any]:
    with open(FALLBACK_PATH, encoding="utf-8") as f:
        snap = json.load(f)
    return {**snap, "live": False}


def rates() -> dict[str, Any]:
    """``{"rates": {code: units per USD}, "updated": unix, "source", "live"}``."""
    try:
        return _live_rates()
    except UpstreamError:
        return _fallback_rates()


def to_usd(amount: float, currency: str) -> float | None:
    """Convert *amount* of *currency* to USD, or ``None`` for unknown codes."""
    if currency == "USD":
        return float(amount)
    per_usd = rates()["rates"].get(currency)
    if not per_usd:
        log.warning("no exchange rate for %s", currency)
        return None
    return amount / float(per_usd)


def meta() -> dict[str, Any]:
    r = rates()
    return {"updated": r.get("updated"), "source": r.get("source"), "live": r.get("live")}
