"""How often is "Buy now or wait?" right?  Replay it on real Steam prices.

For a sample of paid games from data/pool.json it loads each one's US Steam
price log from IsThereAnyDeal (cached in .cache/itad/), then goes back to every
Monday of the past two years.  On each, it works out the verdict from what was
known that day, and checks what the price actually did next.  A fall of 15%
counts as cheaper, the same saving the rules ask before saying wait:

- "Buy now" was right if the price didn't fall that far in the next 60 days.
- "Wait" was right if it did, by the end of the sale it named (or within 60
  days, for "Wait for a sale").

Needs ITAD_API_KEY in .env.  Run from the project root:

    python scripts/backtest_timing.py                 # 200 games
    python scripts/backtest_timing.py --games 400 --saving 0.2 --soon 45

The threshold flags override services/timing.py for the run, so you can try
values before changing them there.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import random
import statistics
import sys
import time
from collections import defaultdict

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from services import itad, pool, timing  # noqa: E402
from services.http import UpstreamError  # noqa: E402

CACHE = os.path.join(ROOT, ".cache", "itad")
HORIZON = 60  # days "Buy now" is held to
YEARS = 2


def load_log(appid: int) -> list[dict] | None:
    """The game's ITAD log, from .cache/itad/ when we have it."""
    path = os.path.join(CACHE, f"{appid}.json")
    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            saved = json.load(f)
    else:
        try:
            log = itad.steam_history(appid)
        except UpstreamError as exc:
            print(f"  {appid}: {exc}")
            return None
        saved = [{**e, "day": e["day"].isoformat()} for e in (log or {}).get("events") or []]
        os.makedirs(CACHE, exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(saved, f)
        time.sleep(0.4)  # two requests a game keeps us far under ITAD's 1,000 per 5 minutes
    return [{**e, "day": dt.date.fromisoformat(e["day"])} for e in saved]


def price_on(runs: list[timing.Span], day: dt.date) -> timing.Span | None:
    return next((r for r in runs if r.start <= day < r.end), None)


def sale_end(runs: list[timing.Span], day: dt.date) -> dt.date | None:
    """When the discount running on *day* ended (Steam tells us this in advance)."""
    end = None
    for r in runs:
        if r.end <= day:
            continue
        if not r.on_sale:
            break
        end = r.end
    return end


def lowest_between(runs: list[timing.Span], start: dt.date, end: dt.date) -> int | None:
    prices = [r.price for r in runs if r.price is not None and r.start < end and r.end > start]
    return min(prices, default=None)


def replay(appid: int, events: list[dict], release: dt.date | None, today: dt.date, results: dict) -> None:
    runs = timing.spans(events, today)
    first = events[0]["day"]
    day = today - dt.timedelta(days=YEARS * 365)
    day += dt.timedelta(days=(7 - day.weekday()) % 7)  # Mondays
    while day <= today - dt.timedelta(days=HORIZON):
        now_run = price_on(runs, day)
        if day > first and now_run and now_run.price:
            now = timing.Now(now_run.price, now_run.regular, now_run.cut,
                             sale_end(runs, day) if now_run.on_sale else None)
            known = [e for e in events if e["day"] <= day]
            v = timing.verdict(known, now, release=release, today=day)
            horizon = day + dt.timedelta(days=HORIZON)
            if v["verdict"] == "wait":
                horizon = max(horizon, dt.date.fromisoformat(v["expected"]["by"]))
            low = lowest_between(runs, day + dt.timedelta(days=1), horizon + dt.timedelta(days=1))
            dropped = low is not None and low <= now.price * (1 - timing.SAVING)
            title = v["title"]
            if title.startswith("Wait for the"):
                title = "Wait for the next big sale"
            key = (v["verdict"], title)
            results[key].append({
                "appid": appid,
                "dropped": dropped,
                "saving": (now.price - low) / now.price if low is not None else 0.0,
                "error": (abs(low - v["expected"]["price"] * 100) / (v["expected"]["price"] * 100)
                          if v["verdict"] == "wait" and low is not None else None),
            })
        day += dt.timedelta(days=7)


def report(results: dict) -> None:
    total = sum(len(v) for v in results.values())
    print(f"\n{total:,} verdicts\n")
    print(f"{'verdict':<34}{'share':>7}{'right':>8}   notes")
    for (kind, title), rows in sorted(results.items(), key=lambda kv: -len(kv[1])):
        n = len(rows)
        dropped = sum(r["dropped"] for r in rows) / n
        if kind == "buy":
            right, note = 1 - dropped, ""
        elif kind == "wait":
            errors = [r["error"] for r in rows if r["error"] is not None]
            savings = [r["saving"] for r in rows if r["dropped"]]
            right = dropped
            note = (f"saved {statistics.mean(savings):.0%} on average when right; "
                    f"expected price off by {statistics.median(errors):.0%} (median)") if savings and errors else ""
        else:
            right, note = None, f"price fell 15%+ within 60 days {dropped:.0%} of the time"
        print(f"{title[:33]:<34}{n / total:>7.0%}{('' if right is None else f'{right:.0%}'):>8}   {note}")
    buys = [r for (k, _), rows in results.items() if k == "buy" for r in rows]
    waits = [r for (k, _), rows in results.items() if k == "wait" for r in rows]
    if buys:
        print(f"\nBuy now overall: right {1 - sum(r['dropped'] for r in buys) / len(buys):.0%} of {len(buys):,}")
    if waits:
        print(f"Wait overall: right {sum(r['dropped'] for r in waits) / len(waits):.0%} of {len(waits):,}")
    every = [r for rows in results.values() for r in rows]
    print(f"Baseline: the price fell 15%+ soon after {sum(r['dropped'] for r in every) / len(every):.0%} "
          f"of all days tested")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--games", type=int, default=200)
    parser.add_argument("--seed", type=int, default=1)
    for name in ("near_low", "near_year", "saving"):
        parser.add_argument(f"--{name.replace('_', '-')}", type=float)
    for name in ("soon",):
        parser.add_argument(f"--{name}", type=int)
    args = parser.parse_args()
    for name in ("near_low", "near_year", "saving", "soon"):
        if getattr(args, name) is not None:
            setattr(timing, name.upper(), getattr(args, name))
    if not itad.enabled():
        sys.exit("Set ITAD_API_KEY in .env first.")

    paid = [g for g in pool.games() if not g.get("is_free")]
    sample = random.Random(args.seed).sample(paid, min(args.games, len(paid)))
    today = timing.steam_today()
    results: dict = defaultdict(list)
    for i, g in enumerate(sample, 1):
        events = load_log(g["appid"])
        if events:
            release = dt.datetime.fromtimestamp(g["release"], dt.timezone.utc).date() if g.get("release") else None
            replay(g["appid"], events, release, today, results)
        if i % 25 == 0:
            print(f"  {i}/{len(sample)} games")
    print(f"Thresholds: near_low {timing.NEAR_LOW}, near_year {timing.NEAR_YEAR}, "
          f"saving {timing.SAVING}, soon {timing.SOON} days")
    report(results)


if __name__ == "__main__":
    main()
