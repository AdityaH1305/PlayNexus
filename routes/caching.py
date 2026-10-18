"""Cache-Control helpers.

On Vercel, a function response with ``s-maxage`` is cached at the edge, so
most visitors never reach Python (or Steam).  Browsers get ``max-age=0`` and
always revalidate, so a deploy shows up immediately.
"""

from __future__ import annotations

from functools import wraps

from flask import g, make_response

DEGRADED_S_MAXAGE = 60


def mark_degraded() -> None:
    """Call from a view when an upstream failed and the page is incomplete,
    so the CDN only keeps it for a minute instead of the usual time."""
    g.cdn_degraded = True


def cdn_cache(s_maxage: int, swr: int = 86400, error_s_maxage: int = 60):
    """Let the CDN keep successful responses for *s_maxage* seconds.

    Stale copies may be served for *swr* more seconds while a fresh one is
    fetched in the background.  404s are cached briefly; anything else isn't.
    Pages marked with :func:`mark_degraded` are kept for a minute at most.
    """

    def decorator(fn):
        @wraps(fn)
        def wrapper(*args, **kwargs):
            resp = make_response(fn(*args, **kwargs))
            if resp.status_code in (200, 301, 302, 308):
                ttl = min(s_maxage, DEGRADED_S_MAXAGE) if g.get("cdn_degraded") else s_maxage
                stale = 0 if g.get("cdn_degraded") else swr
                resp.headers["Cache-Control"] = (
                    f"public, max-age=0, s-maxage={ttl}, stale-while-revalidate={stale}"
                )
            elif resp.status_code == 404:
                resp.headers["Cache-Control"] = f"public, max-age=0, s-maxage={error_s_maxage}"
            else:
                resp.headers["Cache-Control"] = "no-store"
            return resp

        return wrapper

    return decorator


def no_store(fn):
    """For per-visitor responses that must never be shared or kept."""

    @wraps(fn)
    def wrapper(*args, **kwargs):
        resp = make_response(fn(*args, **kwargs))
        resp.headers["Cache-Control"] = "private, no-store"
        return resp

    return wrapper
