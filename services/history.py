"""Steam price history: each tracked game's US price, once a day.

A Vercel cron job (vercel.json) calls /api/cron/snapshot every day at 22:00
UTC.  That records the US price of every paid game in the discovery pool plus
this week's top sellers in Postgres, one row per game per day, so running it
again the same day just refreshes that day's rows.  Game pages read it back
for a price chart and the lowest price in the last 90 days; the deals page
flags sales at or below that low.

22:00 UTC is hours after Steam's 10 a.m. Pacific price changes, so even a
price cached three hours earlier (services.steam keeps them that long) is
already today's.

All of it is optional.  Without DATABASE_URL, or while the database can't be
reached, reads come back empty and pages leave price history out.
"""

from __future__ import annotations

import datetime as dt
import logging
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Iterable

from config import Config
from services import pool, steam
from services.http import UpstreamError, chunks, ttl_cache

log = logging.getLogger(__name__)

CC = "us"  # the store we track; prices are in US cents
WINDOW = 90  # days in "lowest in the last 90 days"
NEAR = 0.10  # within 10% of that low counts as near it
CHART_DAYS = 365  # history sent to game pages
TOP_SELLERS = 100  # this week's best sellers, tracked on top of the pool
BATCH = 50  # apps per Steam price call, the most it takes
WORKERS = 4  # Steam calls in flight at once during a snapshot
RETRY_WITHIN = 60  # seconds: retry batches Steam dropped if we're still this early
HOUR = 3600


class HistoryError(UpstreamError):
    """The price-history database failed or isn't set up."""


def enabled() -> bool:
    """Whether price history is configured (``DATABASE_URL`` is set)."""
    return bool(Config.DATABASE_URL)


def today() -> dt.date:
    return dt.datetime.now(dt.timezone.utc).date()


# ══════════════════════════════════════════════════════════
#  Database
# ══════════════════════════════════════════════════════════

SCHEMA = """
CREATE TABLE IF NOT EXISTS price_snapshots (
    appid         integer NOT NULL,
    cc            text    NOT NULL,
    day           date    NOT NULL,
    final_cents   integer NOT NULL,
    initial_cents integer NOT NULL,
    discount      integer NOT NULL,
    currency      text    NOT NULL,
    PRIMARY KEY (appid, cc, day)
)
"""

# The whole snapshot in one statement.  A rerun on the same day rewrites the
# rows whose price changed since and leaves the rest alone.
UPSERT = """
INSERT INTO price_snapshots (appid, cc, day, final_cents, initial_cents, discount, currency)
SELECT t.appid, %(cc)s::text, %(day)s::date, t.final_cents, t.initial_cents, t.discount, t.currency
FROM unnest(%(appids)s::int[], %(finals)s::int[], %(initials)s::int[], %(discounts)s::int[],
            %(currencies)s::text[]) AS t (appid, final_cents, initial_cents, discount, currency)
ON CONFLICT (appid, cc, day) DO UPDATE SET
    final_cents = EXCLUDED.final_cents,
    initial_cents = EXCLUDED.initial_cents,
    discount = EXCLUDED.discount,
    currency = EXCLUDED.currency
WHERE (price_snapshots.final_cents, price_snapshots.initial_cents, price_snapshots.discount,
       price_snapshots.currency)
    IS DISTINCT FROM (EXCLUDED.final_cents, EXCLUDED.initial_cents, EXCLUDED.discount, EXCLUDED.currency)
"""

POINTS = """
SELECT day, final_cents, initial_cents, discount
FROM price_snapshots
WHERE appid = %(appid)s AND cc = %(cc)s AND day >= %(start)s
ORDER BY day
"""

# The lowest price in the window, and the first day we have for the app at
# all: a 90-day low only counts once we've watched the game for 90 days.
LOWS = """
SELECT appid, MIN(final_cents) FILTER (WHERE day >= %(start)s), MIN(day)
FROM price_snapshots
WHERE cc = %(cc)s AND appid = ANY(%(ids)s::int[])
GROUP BY appid
"""


def _driver():
    try:
        import psycopg  # here, not at the top, so the app still runs without it
    except ImportError:
        raise HistoryError("psycopg isn't installed (pip install -r requirements.txt)") from None
    return psycopg


def _connect(timeout: int = 5):
    """A new autocommit connection.

    Neon's ``DATABASE_URL`` goes through its connection pooler, which can't
    keep prepared statements between transactions, so psycopg never makes any.
    """
    return _driver().connect(
        Config.DATABASE_URL, autocommit=True, connect_timeout=timeout, prepare_threshold=None,
    )


def _query(sql: str, params: dict[str, Any]) -> list[tuple]:
    """Rows for one read.  No table yet (no snapshot has run) means no rows."""
    psycopg = _driver()
    try:
        with _connect() as conn:
            return conn.execute(sql, params).fetchall()
    except psycopg.errors.UndefinedTable:
        return []
    except psycopg.Error as exc:
        raise HistoryError(f"price history read failed: {exc}") from None


def save(day: dt.date, prices: dict[int, dict[str, Any]]) -> int:
    """Store one day's prices (``{appid: row}`` from :func:`fetch_prices`)."""
    psycopg = _driver()
    ids = sorted(prices)
    params = {
        "cc": CC,
        "day": day,
        "appids": ids,
        "finals": [prices[a]["final"] for a in ids],
        "initials": [prices[a]["initial"] for a in ids],
        "discounts": [prices[a]["discount"] for a in ids],
        "currencies": [prices[a]["currency"] for a in ids],
    }
    try:
        # A sleeping Neon database takes a moment to wake, so allow longer here.
        with _connect(timeout=15) as conn:
            conn.execute(SCHEMA)
            conn.execute(UPSERT, params)
    except psycopg.Error as exc:
        raise HistoryError(f"couldn't save the price snapshot: {exc}") from None
    return len(ids)


# ══════════════════════════════════════════════════════════
#  Daily snapshot
# ══════════════════════════════════════════════════════════

def tracked_appids() -> list[int]:
    """Every paid game in the discovery pool, then this week's top sellers."""
    ids = [g["appid"] for g in pool.games() if not g.get("is_free")]
    try:
        ranks = steam.weekly_top_sellers("US", TOP_SELLERS).get("ranks") or []
    except UpstreamError as exc:
        log.warning("price snapshot: no top sellers this time (%s)", exc)
        ranks = []
    ids += [int(r["appid"]) for r in ranks if r.get("appid")]
    return list(dict.fromkeys(ids))


def _row(po: Any) -> dict[str, Any] | None:
    """A ``price_overview`` as a table row, or ``None`` if it isn't a US price."""
    if not isinstance(po, dict) or po.get("currency") != "USD":
        return None
    try:
        final = int(po["final"])
        initial = int(po.get("initial") or final)
        discount = int(po.get("discount_percent") or 0)
    except (KeyError, TypeError, ValueError):
        return None
    return {"final": final, "initial": initial, "discount": discount, "currency": "USD"}


def fetch_prices(appids: list[int]) -> tuple[dict[int, dict[str, Any]], set[int]]:
    """Today's US prices for *appids*, a few batched Steam calls at a time.

    Returns ``{appid: row}`` for apps the US store sells (free and unsold
    apps are left out) and the IDs Steam didn't answer for, after one retry.
    """

    def fetch(batch: list[int]) -> tuple[dict, set[int]]:
        missed: set[int] = set()
        return steam.price_overview(batch, CC, failed=missed), missed

    started = time.monotonic()
    found: dict[int, dict[str, Any]] = {}
    todo, failed = list(appids), set()
    for attempt in range(2):
        failed = set()
        with ThreadPoolExecutor(WORKERS, thread_name_prefix="snapshot") as workers:
            for got, missed in workers.map(fetch, chunks(todo, BATCH)):
                failed |= missed
                for appid, po in got.items():
                    row = None if appid in missed else _row(po)
                    if row:
                        found[appid] = row
        if not failed or attempt or time.monotonic() - started > RETRY_WITHIN:
            break
        todo = sorted(failed)
        time.sleep(2)  # Steam drops requests when pushed; give it a moment
    return found, failed


def snapshot(day: dt.date | None = None) -> dict[str, Any]:
    """Record today's US price for every tracked game.

    Safe to run again: each game has one row per day, which a rerun refreshes.
    Raises :class:`HistoryError` if the database fails, and
    :class:`UpstreamError` if Steam answered for none of the games.
    """
    if not enabled():
        raise HistoryError("DATABASE_URL isn't set")
    started = time.monotonic()
    day = day or today()
    ids = tracked_appids()
    prices, failed = fetch_prices(ids)
    if not prices and failed:
        raise UpstreamError(f"Steam didn't answer for any of {len(ids)} games")
    saved = save(day, prices) if prices else 0
    result = {
        "day": day.isoformat(),
        "tracked": len(ids),
        "saved": saved,
        "not_sold": len(ids) - saved - len(failed),
        "failed": len(failed),
        "seconds": round(time.monotonic() - started, 1),
    }
    log.info("price snapshot: %s", result)
    return result


# ══════════════════════════════════════════════════════════
#  Reading it back
# ══════════════════════════════════════════════════════════

def price_status(now: int | None, low: int, high: int) -> str | None:
    """How today's price (*now*, cents) compares with the window's *low* and *high*.

    ``"low"``: at or below the lowest.  ``"near"``: within 10% of it.
    ``"flat"``: the price hasn't moved at all.  ``None``: none of those.
    """
    if now is None:
        return None
    if now < low:
        return "low"
    if now == low:
        return "flat" if high == low else "low"
    if now <= low * (1 + NEAR):
        return "near"
    return None


def summarize(points: list[dict[str, Any]], day: dt.date, now: int | None) -> tuple[dict | None, str | None]:
    """The lowest price in the 90 days up to *day*, and :func:`price_status`.

    The low has ``full`` set once the history covers all 90 days; until then
    ``since`` is the first day we have.  Ties go to the most recent day.
    """
    start = day - dt.timedelta(days=WINDOW - 1)
    recent = [p for p in points if p["day"] >= start]
    if not recent:
        return None, None
    low = min(recent, key=lambda p: (p["final"], -p["day"].toordinal()))
    high = max(p["final"] for p in recent)
    full = points[0]["day"] <= start
    summary = {
        "final": low["final"],
        "day": low["day"].isoformat(),
        "full": full,
        "since": (start if full else recent[0]["day"]).isoformat(),
    }
    return summary, price_status(now, low["final"], high)


@ttl_cache(ttl=30 * 60, fail_ttl=60)
def _points(appid: int, day: dt.date) -> list[dict[str, Any]]:
    start = day - dt.timedelta(days=CHART_DAYS - 1)
    rows = _query(POINTS, {"appid": appid, "cc": CC, "start": start})
    return [{"day": d, "final": f, "initial": i, "discount": disc} for d, f, i, disc in rows]


def _live_price(appid: int) -> int | None:
    """Today's US price in cents, if the US store sells the game."""
    row = _row(steam.price_overview([appid], CC).get(appid))
    return row["final"] if row else None


def game_history(appid: int) -> dict[str, Any]:
    """Everything a game page shows about price history.

    ``points``: daily US prices in cents, oldest first, up to a year.
    ``low``: the lowest of the last 90 days (see :func:`summarize`).
    ``now`` and ``status``: today's US price and how it compares with that.
    ``degraded`` is set when the database couldn't be read.
    """
    out: dict[str, Any] = {"currency": "USD", "points": [], "low": None, "now": None, "status": None}
    if not enabled():
        return out
    day = today()
    try:
        points = _points(appid, day)
    except UpstreamError:
        return {**out, "degraded": True}
    if not points:
        return out
    now = _live_price(appid)
    if now is None and points[-1]["day"] >= day - dt.timedelta(days=1):
        now = points[-1]["final"]  # Steam didn't answer just now; last night's price will do
    low, status = summarize(points, day, now)
    out.update(
        points=[{**p, "day": p["day"].isoformat()} for p in points],
        low=low,
        now=now,
        status=status,
    )
    return out


@ttl_cache(ttl=HOUR, fail_ttl=120)
def _lows(ids: tuple[int, ...], day: dt.date) -> dict[int, int]:
    start = day - dt.timedelta(days=WINDOW - 1)
    rows = _query(LOWS, {"cc": CC, "ids": list(ids), "start": start})
    return {appid: low for appid, low, first in rows if low is not None and first <= start}


def lows(appids: Iterable[int]) -> dict[int, int]:
    """The lowest US price (cents) of the last 90 days, for each app we've
    tracked for all 90 of them.  Empty when there's no history to go on."""
    ids = tuple(sorted({int(a) for a in appids}))
    if not ids or not enabled():
        return {}
    try:
        return _lows(ids, today())
    except UpstreamError:
        return {}


def at_low(deals: list[dict[str, Any]]) -> set[int]:
    """App IDs of deals (``appid``, ``sale_price`` in dollars) priced at or
    below their 90-day low."""
    known = lows(d["appid"] for d in deals)
    return {
        d["appid"] for d in deals
        if d["appid"] in known and round(d["sale_price"] * 100) <= known[d["appid"]]
    }
