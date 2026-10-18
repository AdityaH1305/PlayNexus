"""Shared HTTP plumbing: one pooled session, JSON GETs, and TTL caches.

Every upstream call in the app goes through :func:`get_json`, so timeouts,
retries and the User-Agent live in one place.  Results are cached with
:func:`ttl_cache` (per-call) or :class:`TTLStore` (per-key, for batched
endpoints).  Failures are cached only briefly so a Steam hiccup doesn't stick
around for the full TTL, and so a struggling upstream isn't hammered.
"""

from __future__ import annotations

import functools
import logging
import re
import threading
import time
from collections import OrderedDict
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Callable, Hashable, Iterable

import requests
from requests.adapters import HTTPAdapter

from config import Config

log = logging.getLogger(__name__)


class UpstreamError(Exception):
    """An upstream API failed, timed out, or returned something unusable."""


_session = requests.Session()
_session.headers.update({"User-Agent": Config.USER_AGENT, "Accept": "application/json"})
_session.mount("https://", HTTPAdapter(pool_connections=16, pool_maxsize=48))

executor = ThreadPoolExecutor(max_workers=Config.MAX_WORKERS, thread_name_prefix="upstream")

_RETRY_STATUSES = {429, 500, 502, 503, 504}
_KEY_RE = re.compile(r"(key=)[^&\s'\"]+", re.IGNORECASE)


def redact(text: str) -> str:
    """Hide API keys that request libraries put in error messages."""
    return _KEY_RE.sub(r"\1REDACTED", str(text))


def get_json(
    url: str,
    params: dict[str, Any] | None = None,
    *,
    timeout: float | None = None,
    retries: int = 1,
) -> Any:
    """GET *url* and return the decoded JSON body.

    Retries once on 429/5xx (honouring a short ``Retry-After``).  Raises
    :class:`UpstreamError` on network errors, other non-2xx statuses, or a body
    that isn't JSON.
    """
    timeout = timeout or Config.HTTP_TIMEOUT
    attempt = 0
    while True:
        try:
            r = _session.get(url, params=params, timeout=timeout)
        except requests.RequestException as exc:
            if attempt < retries:
                attempt += 1
                time.sleep(0.4 * attempt)
                continue
            raise UpstreamError(f"{url}: {redact(exc)}") from None

        if r.status_code in _RETRY_STATUSES and attempt < retries:
            attempt += 1
            wait = _retry_after(r) or 0.6 * attempt
            time.sleep(min(wait, 2.0))
            continue
        if not r.ok:
            raise UpstreamError(f"{url}: HTTP {r.status_code}")
        try:
            return r.json()
        except ValueError:
            raise UpstreamError(f"{url}: invalid JSON") from None


def get_text(url: str, params: dict[str, Any] | None = None, *, timeout: float | None = None) -> str:
    """GET *url* and return the body as text (for XML endpoints)."""
    try:
        r = _session.get(url, params=params, timeout=timeout or Config.HTTP_TIMEOUT)
    except requests.RequestException as exc:
        raise UpstreamError(f"{url}: {redact(exc)}") from None
    if not r.ok:
        raise UpstreamError(f"{url}: HTTP {r.status_code}")
    return r.text


def _retry_after(r: requests.Response) -> float | None:
    try:
        return float(r.headers.get("Retry-After", ""))
    except ValueError:
        return None


# ══════════════════════════════════════════════════════════
#  Caches
# ══════════════════════════════════════════════════════════

_MISSING = object()


class TTLStore:
    """A small thread-safe LRU dict whose entries expire individually."""

    def __init__(self, maxsize: int = 4096):
        self._data: OrderedDict[Hashable, tuple[float, Any]] = OrderedDict()
        self._lock = threading.Lock()
        self._maxsize = maxsize

    def get(self, key: Hashable) -> Any:
        """Return the cached value, or ``_MISSING`` if absent or expired."""
        with self._lock:
            item = self._data.get(key)
            if item is None:
                return _MISSING
            expires, value = item
            if expires < time.monotonic():
                del self._data[key]
                return _MISSING
            self._data.move_to_end(key)
            return value

    def set(self, key: Hashable, value: Any, ttl: float) -> None:
        with self._lock:
            self._data[key] = (time.monotonic() + ttl, value)
            self._data.move_to_end(key)
            while len(self._data) > self._maxsize:
                self._data.popitem(last=False)

    def clear(self) -> None:
        with self._lock:
            self._data.clear()


_all_stores: list[TTLStore] = []


def new_store(maxsize: int = 4096) -> TTLStore:
    store = TTLStore(maxsize)
    _all_stores.append(store)
    return store


def clear_caches() -> None:
    """Empty every cache (used by tests)."""
    for store in _all_stores:
        store.clear()


class _Failure:
    __slots__ = ("exc",)

    def __init__(self, exc: Exception):
        self.exc = exc


def ttl_cache(ttl: float, fail_ttl: float = 30.0, maxsize: int = 2048):
    """Cache a function's result per arguments for *ttl* seconds.

    If the call raises :class:`UpstreamError`, the error is remembered for
    *fail_ttl* seconds and re-raised to every caller in that window.
    Concurrent calls with the same arguments share one upstream request.
    """

    def decorator(fn: Callable):
        store = new_store(maxsize)
        inflight: dict[Hashable, threading.Event] = {}
        lock = threading.Lock()

        @functools.wraps(fn)
        def wrapper(*args, **kwargs):
            key = (args, tuple(sorted(kwargs.items())))
            while True:
                cached = store.get(key)
                if cached is not _MISSING:
                    if isinstance(cached, _Failure):
                        raise cached.exc
                    return cached
                with lock:
                    event = inflight.get(key)
                    leader = event is None
                    if leader:
                        event = inflight[key] = threading.Event()
                if leader:
                    break
                # Another thread is fetching the same thing; wait for it.
                event.wait(timeout=Config.HTTP_TIMEOUT * 3)

            try:
                value = fn(*args, **kwargs)
            except UpstreamError as exc:
                store.set(key, _Failure(exc), fail_ttl)
                log.warning("%s failed: %s", fn.__name__, exc)
                raise
            else:
                store.set(key, value, ttl)
                return value
            finally:
                with lock:
                    inflight.pop(key, None)
                event.set()

        wrapper.cache_clear = store.clear  # type: ignore[attr-defined]
        return wrapper

    return decorator


def cached_batch(
    store: TTLStore,
    keys: Iterable[Hashable],
    fetch: Callable[[list[Hashable]], dict[Hashable, Any]],
    ttl: float,
    failed: set | None = None,
) -> dict[Hashable, Any]:
    """Look up many keys at once, fetching only the misses in one call.

    *fetch* receives the missing keys and returns a mapping for those it found;
    keys it omits are cached as ``None`` (a definite "no data").  If *fetch*
    raises :class:`UpstreamError`, misses come back as ``None`` uncached, and
    are added to *failed* if you pass a set.
    """
    result: dict[Hashable, Any] = {}
    missing: list[Hashable] = []
    for k in keys:
        v = store.get(k)
        if v is _MISSING:
            missing.append(k)
        else:
            result[k] = v
    if missing:
        try:
            fetched = fetch(missing)
        except UpstreamError as exc:
            log.warning("batch fetch failed for %d keys: %s", len(missing), exc)
            fetched = None
            if failed is not None:
                failed.update(missing)
        for k in missing:
            if fetched is None:
                result[k] = None
                continue
            v = fetched.get(k)
            store.set(k, v, ttl)
            result[k] = v
    return result


def chunks(items: list, size: int) -> Iterable[list]:
    for i in range(0, len(items), size):
        yield items[i : i + size]
