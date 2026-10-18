"""Value score: how much enjoyment a game gives for its price, 0–100.

    score = 55 × playtime-per-dollar (log scaled)
          + 35 × positivity above 60 %
          + 10 × confidence from review volume

Playtime is the typical playtime of people who reviewed the game.  Free
games get a floor so a well-liked free game always reads as good value.
"""

from __future__ import annotations

import math
from typing import Any

THRESHOLDS = [(85, "Excellent value"), (70, "Great value"), (55, "Good value"), (40, "Fair value")]

W_PLAYTIME, W_POSITIVITY, W_CONFIDENCE = 0.55, 0.35, 0.10


def label_for(score: int | None) -> str:
    if score is None:
        return "No price yet"
    for floor, label in THRESHOLDS:
        if score >= floor:
            return label
    return "Poor value"


def _parts(stats: dict[str, Any]) -> tuple[float, float, float]:
    """(effective hours, positivity term 0–1, confidence term 0–1)."""
    hours = max(
        float(stats.get("p50_all", 0.0)),
        0.7 * float(stats.get("p75_recent", 0.0)),
        float(stats.get("p50_recent", 0.0)),
    )
    pos_norm = max(0.0, min(1.0, (float(stats.get("positivity", 0.0)) - 0.60) / 0.40))
    total = int(stats.get("total_reviews", 0))
    conf = min(1.0, math.log10(max(10, total)) / 3.0)
    return hours, pos_norm, conf


def _hpd_norm(hours: float, price: float) -> float:
    hpd = hours / max(price, 1.0)
    return max(0.0, min(1.0, math.log10(hpd + 1.0) / math.log10(20.0))) if hpd > 0 else 0.0


def _raw(hours: float, pos_norm: float, conf: float, price: float) -> float:
    return W_PLAYTIME * _hpd_norm(hours, price) + W_POSITIVITY * pos_norm + W_CONFIDENCE * conf


def compute_value_score(
    usd_price: float | None, stats: dict[str, Any], *, is_free: bool = False
) -> tuple[int | None, str, dict[str, Any]]:
    """Return ``(score, label, breakdown)``.

    *usd_price* ``None`` means the price is unknown (not for sale, or Steam
    didn't answer): the score is ``None`` rather than treating it as free.
    """
    hours, pos_norm, conf = _parts(stats)
    positivity = float(stats.get("positivity", 0.0))

    if usd_price is None and not is_free:
        return None, label_for(None), _breakdown(hours, 0.0, pos_norm, conf, None, False)

    # Prices under $1 (and free games) are treated as $1 to avoid dividing by zero.
    price = 0.0 if is_free else float(usd_price)
    hpd_norm = _hpd_norm(hours, max(price, 1.0))
    raw = W_PLAYTIME * hpd_norm + W_POSITIVITY * pos_norm + W_CONFIDENCE * conf
    if is_free and positivity > 0.5:
        # Free games are good value by definition, if people like them.
        raw = max(raw, 0.60 + 0.30 * pos_norm + 0.10 * conf)
    score = int(round(max(0.0, min(100.0, 100.0 * raw))))
    return score, label_for(score), _breakdown(hours, hpd_norm, pos_norm, conf, price, is_free)


def _breakdown(hours, hpd_norm, pos_norm, conf, price, is_free) -> dict[str, Any]:
    hpd = (hours / max(price, 1.0)) if price else 0.0
    return {
        "effective_hours": round(hours, 1),
        "hours_per_dollar": round(hpd, 2),
        "cost_per_hour": round(price / hours, 2) if price and hours >= 0.5 else None,
        # Points out of each component's maximum (55 / 35 / 10).
        "playtime_pts": round(100 * W_PLAYTIME * hpd_norm, 1),
        "positivity_pts": round(100 * W_POSITIVITY * pos_norm, 1),
        "confidence_pts": round(100 * W_CONFIDENCE * conf, 1),
        "is_free": is_free,
    }


def worth_it_price(stats: dict[str, Any], target: int) -> float | None:
    """Highest USD price at which the game still scores at least *target*.

    Returns ``None`` if it can't reach *target* even at $1 (the floor price
    the formula uses).
    """
    hours, pos_norm, conf = _parts(stats)
    # Scores are rounded, so x.5 counts — but Python rounds halves to even,
    # so aim a hair above the boundary.
    goal = (target - 0.5) / 100.0 + 1e-6
    if _raw(hours, pos_norm, conf, 1.0) < goal:
        return None
    lo, hi = 1.0, 200.0
    if _raw(hours, pos_norm, conf, hi) >= goal:
        return hi
    for _ in range(40):  # score falls as price rises, so bisect
        mid = (lo + hi) / 2
        if _raw(hours, pos_norm, conf, mid) >= goal:
            lo = mid
        else:
            hi = mid
    return math.floor(lo * 100) / 100  # round down: the price must still qualify
