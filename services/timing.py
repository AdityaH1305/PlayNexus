"""Buy now or wait?  A verdict on a game's US Steam price.

It weighs:
- the game's own US Steam price history (services.itad);
- Steam's seasonal sales (services.steam_sales), which most games join;
- Steam's discount rules (Steamworks, "Discounting"): no discount for 30 days
  after a release or a base-price rise, and 30 days between discounts, except
  that seasonal sales are exempt either side.  A game whose cooldown ends
  partway through a seasonal sale can still join it;
- when the current discount ends, from Steam.

Valve also tells developers to deepen discounts over time, so the cut in a
game's last seasonal sale is a fair guess at its next one.

:func:`verdict` is pure, so tests and scripts/backtest_timing.py can replay any
day of a game's history.  :func:`assess` gathers the inputs for one game.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from typing import Any

from services import itad, steam, steam_sales
from services.game import release_iso
from services.http import UpstreamError, executor

COOLDOWN = dt.timedelta(days=30)  # Steam's gap after a release, a base-price rise or a discount
NEAR_LOW = 0.02  # within 2% of the all-time low counts as matching it
NEAR_YEAR = 0.05  # within 5% of the past year's low
SAVING = 0.15  # waiting has to save at least this much
SOON = 60  # days ahead that "wait" can point to
RECORD = 4  # seasonal sales looked back over
# Sales at least SAVING below today's price in the past year that make another
# within SOON days likely: in the backtest, 8 or more did 92% of the time.
FREQUENT = 8
FREQUENT_COOLDOWN = 45  # days: Steam has to allow its next ordinary discount by then
MAX_FACTS = 4
YEAR = dt.timedelta(days=365)
ONE_DAY = dt.timedelta(days=1)

MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
MONTH_NAMES = ["January", "February", "March", "April", "May", "June", "July", "August",
               "September", "October", "November", "December"]


@dataclass(frozen=True)
class Now:
    """Steam's US price today, in cents.  ``ends`` is the day the discount ends."""

    price: int
    regular: int
    cut: int = 0
    ends: dt.date | None = None


@dataclass(frozen=True)
class Span:
    """One price, in force from ``start`` up to (not including) ``end``."""

    start: dt.date
    end: dt.date
    price: int | None  # None while Steam wasn't selling the game
    regular: int | None

    @property
    def on_sale(self) -> bool:
        return self.price is not None and bool(self.regular) and self.price < self.regular

    @property
    def cut(self) -> int:
        return round(100 * (1 - self.price / self.regular)) if self.on_sale else 0


@dataclass(frozen=True)
class Period:
    """A run of discounted days, ``start`` up to (not including) ``end``."""

    start: dt.date
    end: dt.date
    low: int
    cut: int


# ── Reading the history ─────────────────────────────────────

def spans(events: list[dict[str, Any]], today: dt.date, now: Now | None = None) -> list[Span]:
    """The price in force from day to day up to *today*, in runs.  Several
    changes on one day keep the last; *now*, Steam's own price, is today's
    price in case the log hasn't caught up."""
    by_day: dict[dt.date, tuple[int | None, int | None]] = {}
    for e in events:
        if e["day"] <= today:
            by_day[e["day"]] = (e["price"], e["regular"])
    if now is not None:
        by_day[today] = (now.price, now.regular)
    days = sorted(by_day)
    out: list[Span] = []
    for i, day in enumerate(days):
        end = days[i + 1] if i + 1 < len(days) else today + ONE_DAY
        price, regular = by_day[day]
        if out and (out[-1].price, out[-1].regular) == (price, regular):
            out[-1] = Span(out[-1].start, end, price, regular)
        else:
            out.append(Span(day, end, price, regular))
    return out


def sale_periods(runs: list[Span]) -> list[Period]:
    """Runs of discounted days, with each one's lowest price and deepest cut."""
    out: list[Period] = []
    for s in runs:
        if not s.on_sale:
            continue
        if out and out[-1].end == s.start:
            p = out[-1]
            out[-1] = Period(p.start, s.end, min(p.low, s.price), max(p.cut, s.cut))
        else:
            out.append(Period(s.start, s.end, s.price, s.cut))
    return out


def price_rises(runs: list[Span]) -> list[dt.date]:
    """The days the base price went up."""
    out, last = [], None
    for s in runs:
        if s.regular is None:
            continue
        if last is not None and s.regular > last:
            out.append(s.start)
        last = s.regular
    return out


def allowed_from(release: dt.date | None, rises: list[dt.date], day: dt.date) -> dt.date:
    """The first day Steam lets the game take a discount, as of *day*: 30 days
    after its release and after its latest base-price rise."""
    dates = [r + COOLDOWN for r in rises if r <= day]
    if release:
        dates.append(release + COOLDOWN)
    return max(dates, default=dt.date.min)


def lowest(runs: list[Span], since: dt.date | None = None) -> Span | None:
    """The cheapest run from *since* on; ties go to the most recent."""
    priced = [s for s in runs if s.price is not None and (since is None or s.end > since)]
    return min(priced, key=lambda s: (s.price, -s.start.toordinal()), default=None)


def _discounted_days(runs: list[Span], start: dt.date, end: dt.date) -> int:
    return sum(
        (min(s.end, end) - max(s.start, start)).days for s in runs if s.on_sale and s.start < end and s.end > start
    )


def big_sale_record(runs: list[Span], release: dt.date | None, rises: list[dt.date],
                    today: dt.date, since: dt.date) -> list[dict[str, Any]]:
    """The game's last few seasonal sales, most recent first: ``{sale, joined,
    cut, low}``.  Only sales the history covers and the game could join count.
    It joined one if it was discounted for at least half the days it could
    have been, so a weeklong deal that just touches a sale doesn't count."""
    out = []
    for sale in steam_sales.past(today):
        if sale["start"] < since:
            break
        start = max(sale["start"], allowed_from(release, rises, sale["start"]))
        if start >= sale["end"]:
            continue  # too new, or just after a price rise
        window = [s for s in runs if s.on_sale and s.start < sale["end"] and s.end > start]
        joined = 2 * _discounted_days(runs, start, sale["end"]) >= (sale["end"] - start).days
        cheapest = min(window, key=lambda s: s.price) if joined and window else None
        out.append({
            "sale": sale,
            "joined": joined,
            "cut": max(s.cut for s in window) if cheapest else 0,
            "low": cheapest.price if cheapest else None,
            "regular": cheapest.regular if cheapest else None,
        })
        if len(out) == RECORD:
            break
    return out


def _seasonal(period: Period) -> bool:
    """Whether a discount was a seasonal sale's own, which leaves no cooldown."""
    return any(
        s["start"] <= period.start and period.end <= s["end"] + ONE_DAY
        for s in steam_sales.between(period.start, period.end)
    )


def next_regular(periods: list[Period], now: Now, release: dt.date | None,
                 rises: list[dt.date], today: dt.date) -> dt.date | None:
    """The first day after *today* that Steam allows the game an ordinary
    (not seasonal) discount, or ``None`` if nothing stands in the way."""
    dates = [allowed_from(release, rises, today)]
    custom = [p for p in periods if not _seasonal(p)]
    if custom:
        last = custom[-1]
        if last.end > today:  # running now: its cooldown starts when it ends
            if now.ends:
                dates.append(now.ends + COOLDOWN)
        else:
            dates.append(last.end + COOLDOWN)
    first = max(dates)
    return first if first > today else None


# ── Words ───────────────────────────────────────────────────

def _usd(cents: int) -> str:
    return f"${cents / 100:,.2f}"


def _day(d: dt.date, today: dt.date) -> str:
    """"30 Oct", with the year when it isn't this year, like the site's charts."""
    return f"{d.day} {MONTHS[d.month - 1]}" + ("" if d.year == today.year else f" {d.year}")


def _month(d: dt.date) -> str:
    return f"{MONTH_NAMES[d.month - 1]} {d.year}"


def _when(run: Span) -> str:
    """"in the Summer Sale 2026" for a discount during one, else "in March 2025"."""
    sale = next((s for s in steam_sales.between(run.start, run.end)
                 if run.on_sale and s["start"] <= run.start and run.end <= s["end"] + ONE_DAY), None)
    return f"in the {steam_sales.label(sale)}" if sale else f"in {_month(run.start)}"


def _years(days: int) -> str:
    years = days // 365
    return "over a year" if years < 2 else f"{years} years"


def _facts(now: Now, today: dt.date, *, low: Span | None = None, record: list[dict] = (),
           big_now: dict | None = None, big_next: dict | None = None,
           regular_next: dt.date | None = None) -> list[dict[str, str]]:
    facts = []
    if now.cut:
        until = f" until {_day(now.ends, today)}" if now.ends else " right now"
        facts.append({"kind": "now", "icon": "percent", "text": f"−{now.cut}% ({_usd(now.price)}){until}."})
    else:
        facts.append({"kind": "now", "icon": "percent", "text": f"Now {_usd(now.price)}, not on sale."})
    if low and low.price < now.price:
        facts.append({"kind": "low", "icon": "down",
                      "text": f"Lowest ever on Steam: {_usd(low.price)} (−{low.cut}%), {_when(low)}."})
    if record:
        joined = [r for r in record if r["joined"]]
        if len(record) == 1:
            text = (f"On sale in its last big Steam sale, at −{joined[0]['cut']}%." if joined
                    else "Not discounted in its last big Steam sale.")
        elif joined:
            text = (f"On sale in {len(joined)} of its last {len(record)} big Steam sales, "
                    f"most recently −{joined[0]['cut']}%.")
        else:
            text = f"Not discounted in any of its last {len(record)} big Steam sales."
        facts.append({"kind": "record", "icon": "chart", "text": text})
    if big_now and now.cut:
        facts.append({"kind": "sale_ends", "icon": "clock",
                      "text": f"The {big_now['name']} ends {_day(big_now['end'], today)}."})
    elif big_next:
        facts.append({"kind": "next_sale", "icon": "clock",
                      "text": f"{big_next['name']}: {_day(big_next['start'], today)} – {_day(big_next['end'], today)}."})
    if regular_next:
        facts.append({"kind": "cooldown", "icon": "info",
                      "text": f"Steam won't allow another discount before {_day(regular_next, today)}, "
                              f"except in a big seasonal sale."})
    return facts[:MAX_FACTS]


def _sale_json(sale: dict | None) -> dict[str, str] | None:
    if not sale:
        return None
    return {"name": sale["name"], "start": sale["start"].isoformat(), "end": sale["end"].isoformat()}


# ── The verdict ─────────────────────────────────────────────

def verdict(events: list[dict[str, Any]], now: Now, *, release: dt.date | None, today: dt.date) -> dict[str, Any]:
    """Buy now, wait or either, with the reasons: ``{verdict, title, summary,
    facts, expected, next_sale, since}``.

    *events* is the game's US Steam price log (``{day, price, regular}`` in
    cents, oldest first, as services.itad returns it) up to *today*; *now* is
    Steam's price today.  The first rule that fits decides.
    """
    runs = spans(events, today, now)
    rises = price_rises(runs)
    allowed = allowed_from(release, rises, today)
    big_now = steam_sales.running(today)
    big_next = steam_sales.upcoming(today, allowed)
    on_sale = now.cut > 0

    def result(kind: str, title: str, summary: str, facts: list, expected: dict | None = None) -> dict[str, Any]:
        return {"verdict": kind, "title": title, "summary": summary, "facts": facts, "expected": expected,
                "next_sale": _sale_json(big_next), "since": since.isoformat() if events else None}

    if not events:
        since = None
        facts = _facts(now, today, big_now=big_now, big_next=big_next,
                       regular_next=allowed if allowed > today else None)
        return result("unknown", "Can't tell yet", "We don't have this game's Steam price history.", facts)

    since = min(e["day"] for e in events)
    periods = sale_periods(runs)
    low = lowest(runs)
    low_year = lowest(runs, today - YEAR)
    # The facts quote the cheapest sale, not an old base price (Factorio was $20 in 2016).
    low_sale = lowest([r for r in runs if r.on_sale])
    record = big_sale_record(runs, release, rises, today, since)
    joined = [r for r in record if r["joined"]]
    expected_cut = joined[0]["cut"] if joined else None
    expected = None
    if joined:
        # Its last big-sale price, or that cut on today's base price if the price
        # has changed since (Steam rounds discounted prices down to the cent).
        last = joined[0]
        expected = last["low"] if last["regular"] == now.regular else now.regular * (100 - expected_cut) // 100
    reliable = len(record) >= 2 and len(joined) >= min(3, len(record))
    soon = big_next is not None and (big_next["start"] - today).days <= SOON
    regular_next = next_regular(periods, now, release, rises, today)
    facts = _facts(now, today, low=low_sale, record=record, big_now=big_now, big_next=big_next,
                   regular_next=regular_next)

    # 1. As cheap as it has ever been.
    if on_sale and now.price <= low.price * (1 + NEAR_LOW):
        return result("buy", "Buy now", "This is the lowest price it has ever had on Steam.", facts)

    # 2. In a big sale now, at least as deep as last time.
    if big_now and on_sale and (expected_cut is None or now.cut >= expected_cut):
        summary = (f"This is its {big_now['name']} price, as low as in its last big sale."
                   if expected_cut else f"It's in the {big_now['name']}, which ends {_day(big_now['end'], today)}.")
        return result("buy", "Buy now", summary, facts)

    # 3. A big sale it reliably joins is coming, and should save a good amount.
    if soon and reliable and expected is not None and expected <= now.price * (1 - SAVING):
        summary = (f"It was −{expected_cut}% in {len(joined)} of its last {len(record)} big Steam sales, "
                   f"so expect about {_usd(expected)}.")
        return result("wait", f"Wait for the {big_next['name']}", summary, facts,
                      {"price": expected / 100, "cut": expected_cut, "by": big_next["end"].isoformat()})

    # 4. It goes on sale so often that another good one is likely soon.
    deep = [p for p in periods if today - YEAR < p.end <= today and p.low <= now.price * (1 - SAVING)]
    if len(deep) >= FREQUENT and (regular_next is None or (regular_next - today).days <= FREQUENT_COOLDOWN):
        typical = sorted(p.low for p in deep)[len(deep) // 2]
        summary = (f"It went on sale for {_usd(max(p.low for p in deep))} or less {len(deep)} times in the "
                   f"past year, so another sale within two months is likely.")
        return result("wait", "Wait for a sale", summary, facts,
                      {"price": typical / 100, "cut": round(100 * (1 - typical / now.regular)),
                       "by": (today + dt.timedelta(days=SOON)).isoformat()})

    # 5. The lowest in a year.
    if on_sale and low_year and now.price <= low_year.price * (1 + NEAR_YEAR):
        return result("buy", "Buy now", "It's the lowest price in the past year.", facts)

    # 6. Never discounted.
    if not periods:
        start = max(since, release + COOLDOWN) if release else since
        if (today - start).days >= 365:
            return result("buy", "No need to wait",
                          f"It has never been on sale on Steam, in {_years((today - since).days)} of price records.",
                          facts)
        came_out = f" since it came out on {_day(release, today)}" if release else " yet"
        return result("either", "No sale yet", f"It hasn't been discounted{came_out}.", facts)

    # 7. It has been a good deal cheaper, but that isn't likely soon.
    lately = expected or (low_year.price if low_year else None)
    if lately is not None and lately <= now.price * (1 - SAVING):
        recent = [p for p in periods if p.end > today - YEAR] or periods[-1:]
        best = max(recent, key=lambda p: (p.cut, p.start))
        if soon and not reliable:
            why = f"it was on sale in only {len(joined)} of its last {len(record)} big Steam sales"
        elif soon:
            why = f"its big-sale discounts are smaller, −{expected_cut}% last time"
        elif big_next:
            why = f"the next big sale, the {big_next['name']}, isn't until {_day(big_next['start'], today)}"
        else:
            why = "no big Steam sale has been announced yet"
        return result("either", "Either is fine", f"It was −{best.cut}% in {_month(best.start)}, but {why}.", facts)

    # 7, continued: its discounts are small (a deeper one still came a quarter of the
    # time in the backtest, too often for "Buy now").  8: it's rarely discounted.
    recent = [p for p in periods if p.end > today - YEAR]
    if recent:
        return result("either", "Either is fine",
                      f"Its discounts are small: −{max(p.cut for p in recent)}% at most in the past year.", facts)
    return result("buy", "Buy now", f"It's rarely discounted: the last sale was in {_month(periods[-1].start)}.", facts)


# ── One game ────────────────────────────────────────────────

def steam_today() -> dt.date:
    return itad.steam_day(dt.datetime.now(dt.timezone.utc))


def _quietly(fn, *args) -> tuple[Any, bool]:
    """``(result, failed)``: an upstream failure becomes ``(None, True)``."""
    try:
        return fn(*args), False
    except UpstreamError:
        return None, True


def _us_price(appid: int) -> dict[str, Any] | None:
    missed: set[int] = set()
    po = steam.price_overview([appid], "us", failed=missed).get(appid)
    if appid in missed:
        raise UpstreamError("no US price from Steam")
    return po if isinstance(po, dict) and po.get("currency") == "USD" else None


def assess(appid: int) -> dict[str, Any] | None:
    """The verdict for one game, plus its ``source`` and ``degraded`` (set when
    ITAD or Steam failed, so the answer can be cached briefly).  ``None`` when
    there's nothing to judge: free, not out yet, or not sold in the US.
    Raises :class:`UpstreamError` if Steam can't be reached."""
    details = steam.appdetails(appid, "us")
    if not details or details.get("is_free") or (details.get("release_date") or {}).get("coming_soon"):
        return None
    packages = details.get("packages") or []
    f_price = executor.submit(_us_price, appid)
    f_ends = executor.submit(_quietly, steam.discount_end, appid, packages[0] if packages else None)
    f_log = executor.submit(_quietly, itad.steam_history, appid)
    po = f_price.result()
    (ends, ends_failed), (log, log_failed) = f_ends.result(), f_log.result()
    if not po or not po.get("final"):
        return None

    today = steam_today()
    cut = int(po.get("discount_percent") or 0)
    now = Now(
        price=int(po["final"]),
        regular=int(po.get("initial") or po["final"]),
        cut=cut,
        ends=itad.steam_day(dt.datetime.fromtimestamp(ends, dt.timezone.utc)) if cut and ends else None,
    )
    released = release_iso((details.get("release_date") or {}).get("date") or "")
    out = verdict(
        (log or {}).get("events") or [], now,
        release=dt.date.fromisoformat(released) if released else None, today=today,
    )
    out["source"] = {"name": "IsThereAnyDeal", "url": log.get("url")} if log else None
    out["degraded"] = ends_failed or log_failed
    return out
