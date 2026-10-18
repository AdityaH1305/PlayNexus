import threading
import time

import pytest

from services.http import TTLStore, UpstreamError, cached_batch, ttl_cache


def test_ttl_cache_reuses_results_until_expiry():
    calls = []

    @ttl_cache(ttl=0.05)
    def f(x):
        calls.append(x)
        return x * 2

    assert f(2) == 4 and f(2) == 4
    assert calls == [2]
    time.sleep(0.07)
    assert f(2) == 4
    assert calls == [2, 2]


def test_ttl_cache_remembers_failures_only_briefly():
    calls = []

    @ttl_cache(ttl=60, fail_ttl=0.05)
    def f():
        calls.append(1)
        raise UpstreamError("down")

    for _ in range(3):
        with pytest.raises(UpstreamError):
            f()
    assert len(calls) == 1  # failure was cached
    time.sleep(0.07)
    with pytest.raises(UpstreamError):
        f()
    assert len(calls) == 2  # ...but not for the full TTL


def test_ttl_cache_coalesces_concurrent_calls():
    calls = []
    gate = threading.Event()

    @ttl_cache(ttl=60)
    def slow():
        calls.append(1)
        gate.wait(1)
        return "done"

    results = []
    threads = [threading.Thread(target=lambda: results.append(slow())) for _ in range(5)]
    for t in threads:
        t.start()
    time.sleep(0.05)
    gate.set()
    for t in threads:
        t.join()
    assert results == ["done"] * 5
    assert len(calls) == 1


def test_cached_batch_fetches_only_misses():
    store = TTLStore()
    fetched = []

    def fetch(keys):
        fetched.append(list(keys))
        return {k: k * 10 for k in keys if k != 3}

    assert cached_batch(store, [1, 2, 3], fetch, ttl=60) == {1: 10, 2: 20, 3: None}
    assert cached_batch(store, [1, 3, 4], fetch, ttl=60) == {1: 10, 3: None, 4: 40}
    assert fetched == [[1, 2, 3], [4]]  # 3 was cached as a definite "none"


def test_cached_batch_does_not_cache_upstream_errors():
    store = TTLStore()

    def broken(keys):
        raise UpstreamError("nope")

    assert cached_batch(store, [1], broken, ttl=60) == {1: None}
    assert cached_batch(store, [1], lambda keys: {1: "ok"}, ttl=60) == {1: "ok"}
