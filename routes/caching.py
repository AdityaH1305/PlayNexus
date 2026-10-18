"""Cache-Control helpers.

On Vercel, a function response with ``s-maxage`` is cached at the edge, so
most visitors never reach Python (or Steam).  Browsers get ``max-age=0`` and
always revalidate, so a deploy shows up immediately.
"""

from __future__ import annotations

from functools import wraps

from flask import make_response


def cdn_cache(s_maxage: int, swr: int = 86400, error_s_maxage: int = 60):
    """Let the CDN keep successful responses for *s_maxage* seconds.

    Stale copies may be served for *swr* more seconds while a fresh one is
    fetched in the background.  404s are cached briefly; anything else isn't.
    """

    def decorator(fn):
        @wraps(fn)
        def wrapper(*args, **kwargs):
            resp = make_response(fn(*args, **kwargs))
            if resp.status_code in (200, 301, 302, 308):
                resp.headers["Cache-Control"] = (
                    f"public, max-age=0, s-maxage={s_maxage}, stale-while-revalidate={swr}"
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
