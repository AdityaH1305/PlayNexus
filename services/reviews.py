"""Review statistics: positivity, totals, and playtime among reviewers.

Three Steam calls (summary, one page of "all", one page of "recent") run in
parallel through the shared executor.
"""

from __future__ import annotations

import statistics
from concurrent.futures import Future
from typing import Any

from services import steam
from services.http import UpstreamError, executor


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


def submit_review_calls(appid: int) -> tuple[Future, Future, Future]:
    """Start the three review requests; finish with :func:`assemble_review_stats`."""
    return (
        executor.submit(_safe, steam.review_summary, appid, default=None),
        executor.submit(_safe, steam.reviews_page, appid, "all", default=[]),
        executor.submit(_safe, steam.reviews_page, appid, "recent", default=[]),
    )


def get_review_stats(appid: int) -> dict[str, Any]:
    return assemble_review_stats(submit_review_calls(appid))


def assemble_review_stats(futures: tuple[Future, Future, Future]) -> dict[str, Any]:
    """Keys: ``positivity``, ``total_reviews``, ``total_positive``,
    ``total_negative``, ``review_label``, ``p50_all``, ``p50_recent``,
    ``p75_recent``, ``recent_positivity``, ``recent_count`` and ``available``."""
    summary, reviews_all, reviews_recent = (f.result() for f in futures)

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
    }
