"""Review statistics: positivity, totals, playtime among reviewers, and how
the last 30 days compare with the rest.

Four Steam calls (summary, one page of "all", one page of "recent", the
review histogram) run in parallel through the shared executor.
"""

from __future__ import annotations

import statistics
from concurrent.futures import Future
from typing import Any

from services import steam
from services.http import UpstreamError, executor

# When the game page warns that recent reviews differ from the rest.
TREND_MIN_REVIEWS = 50  # reviews in the last 30 days
TREND_HISTORY = 4  # all-time reviews must be at least this many times as many
TREND_SHIFT = 15  # points between the last 30 days and the histogram's all-time figure
TREND_VS_OVERALL = 10  # points, in the same direction, from the overall score the page shows


def _extract_hours(reviews: list[dict]) -> dict[str, float]:
    """Median and 75th-percentile playtime (hours) among *reviews*' authors."""
    mins = [rev.get("author", {}).get("playtime_forever", 0) for rev in reviews]
    hrs = sorted(m / 60.0 for m in mins if isinstance(m, (int, float)) and m >= 0)
    if not hrs:
        return {"p50": 0.0, "p75": 0.0}
    p50 = statistics.median(hrs)
    idx75 = max(0, min(len(hrs) - 1, int(round(0.75 * (len(hrs) - 1)))))
    return {"p50": float(p50), "p75": float(hrs[idx75])}


def _safe(fn, *args, default):
    try:
        return fn(*args)
    except UpstreamError:
        return default


def window_totals(rows: list[dict] | None) -> tuple[int, int]:
    """Up and down votes summed over review histogram rows (``recent`` or ``rollups``)."""
    rows = rows or []
    return (
        sum(r.get("recommendations_up", 0) for r in rows),
        sum(r.get("recommendations_down", 0) for r in rows),
    )


def trend(histogram: dict[str, Any] | None, overall_pct: float) -> dict[str, Any] | None:
    """How the last 30 days of reviews compare with the rest, when they differ
    sharply: ``{direction, recent_pct, recent_count, overall_pct}``, else ``None``.

    Steam's histogram doesn't count quite the same reviews as the summary
    behind the overall score (Helldivers 2: 62% all-time against 75%), so the
    last 30 days are measured against the histogram's own all-time figure,
    and the gap has to hold against *overall_pct* as well.
    """
    if not histogram:
        return None
    up, down = window_totals(histogram.get("recent"))
    all_up, all_down = window_totals(histogram.get("rollups"))
    count, all_count = up + down, all_up + all_down
    # Too few to judge, or a game so new that its last 30 days are most of its reviews.
    if count < TREND_MIN_REVIEWS or all_count < TREND_HISTORY * count:
        return None
    recent_pct = 100 * up / count
    shift = recent_pct - 100 * all_up / all_count
    gap = recent_pct - overall_pct
    if abs(shift) < TREND_SHIFT or abs(gap) < TREND_VS_OVERALL or (shift > 0) != (gap > 0):
        return None
    return {
        "direction": "better" if shift > 0 else "worse",
        "recent_pct": round(recent_pct),
        "recent_count": count,
        "overall_pct": round(overall_pct),
    }


def submit_review_calls(appid: int) -> tuple[Future, Future, Future, Future]:
    """Start the four review requests; finish with :func:`assemble_review_stats`."""
    return (
        executor.submit(_safe, steam.review_summary, appid, default=None),
        executor.submit(_safe, steam.reviews_page, appid, "all", default=[]),
        executor.submit(_safe, steam.reviews_page, appid, "recent", default=[]),
        executor.submit(_safe, steam.review_histogram, appid, default=None),
    )


def get_review_stats(appid: int) -> dict[str, Any]:
    return assemble_review_stats(submit_review_calls(appid))


def assemble_review_stats(futures: tuple[Future, Future, Future, Future]) -> dict[str, Any]:
    """Keys: ``positivity``, ``total_reviews``, ``total_positive``,
    ``total_negative``, ``review_label``, ``p50_all``, ``p50_recent``,
    ``p75_recent``, ``recent_positivity``, ``recent_count``, ``trend`` (see
    :func:`trend`) and ``available``."""
    summary, reviews_all, reviews_recent, histogram = (f.result() for f in futures)

    pos = int((summary or {}).get("total_positive", 0))
    neg = int((summary or {}).get("total_negative", 0))
    total = pos + neg

    recent_up = sum(1 for r in reviews_recent if r.get("voted_up"))
    stats_recent = _extract_hours(reviews_recent)

    return {
        "available": summary is not None,
        "positivity": (pos / total) if total else 0.0,
        "total_reviews": total,
        "total_positive": pos,
        "total_negative": neg,
        "review_label": (summary or {}).get("review_score_desc") or "",
        "p50_all": _extract_hours(reviews_all)["p50"],
        "p50_recent": stats_recent["p50"],
        "p75_recent": stats_recent["p75"],
        "recent_positivity": (recent_up / len(reviews_recent)) if reviews_recent else 0.0,
        "recent_count": len(reviews_recent),
        # Only against Steam's own summary: the store-item fallback isn't comparable.
        "trend": trend(histogram, 100 * pos / total) if total else None,
    }
