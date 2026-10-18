"""Build the data snapshots PlayNexus ships with.

Run from the project root:

    python scripts/build_data.py fx      # exchange-rate fallback (no key)
    python scripts/build_data.py tags    # Steam tag names (no key)
    python scripts/build_data.py pool    # discovery pool with playtime (no key, ~15 min)
    python scripts/build_data.py index   # games-only search index (needs STEAM_API_KEY)
    python scripts/build_data.py all     # everything above

Raw upstream responses are cached in .cache/ so an interrupted run resumes
where it stopped.  Delete .cache/ to force fresh data.
"""

from __future__ import annotations

import argparse
import gzip
import json
import math
import os
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from config import Config  # noqa: E402
from services import steam  # noqa: E402
from services.fx import FX_URL  # noqa: E402
from services.http import UpstreamError, get_json  # noqa: E402
from services.search import is_junk_name  # noqa: E402

DATA = Config.DATA_DIR
CACHE = os.path.join(ROOT, ".cache")


def _write_json(path: str, obj, *, gz: bool = False) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    text = json.dumps(obj, ensure_ascii=False, separators=(",", ":"))
    if gz:
        with gzip.open(path, "wt", encoding="utf-8", compresslevel=9) as f:
            f.write(text)
    else:
        with open(path, "w", encoding="utf-8") as f:
            f.write(text)
    print(f"  wrote {os.path.relpath(path, ROOT)} ({os.path.getsize(path) / 1024:.0f} KB)")


def _cached(name: str, produce):
    """Return JSON cached under .cache/<name>, producing it on a miss."""
    path = os.path.join(CACHE, name)
    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    value = produce()
    os.makedirs(CACHE, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(value, f)
    return value


# ── Exchange rates ────────────────────────────────────────

def build_fx() -> None:
    print("Exchange rates")
    data = get_json(FX_URL)
    if data.get("result") != "success":
        raise SystemExit("exchange-rate API did not return success")
    _write_json(
        os.path.join(DATA, "fx_fallback.json"),
        {
            "rates": data["rates"],
            "updated": data.get("time_last_update_unix"),
            "source": "ExchangeRate-API (snapshot)",
        },
    )


# ── Tag names ─────────────────────────────────────────────

def build_tags() -> None:
    print("Steam tags")
    names = steam.tag_names()
    _write_json(os.path.join(DATA, "tags.json"), {str(k): v for k, v in sorted(names.items())})


# ── Search index ──────────────────────────────────────────

def _app_list_page(key: str, last_appid: int) -> dict:
    return get_json(
        "https://api.steampowered.com/IStoreService/GetAppList/v1/",
        {
            "key": key,
            "include_games": "true",
            "include_dlc": "false",
            "include_software": "false",
            "include_videos": "false",
            "include_hardware": "false",
            "max_results": 50000,
            "last_appid": last_appid,
        },
        timeout=60,
    )


def fetch_games(key: str) -> list[dict]:
    apps: list[dict] = []
    last = 0
    page = 0
    while True:
        resp = _cached(f"applist_{last}.json", lambda: _app_list_page(key, last)).get("response") or {}
        batch = resp.get("apps") or []
        apps.extend(batch)
        page += 1
        print(f"  app list page {page}: {len(batch)} apps (total {len(apps)})")
        if not resp.get("have_more_results") or not batch:
            return apps
        last = resp.get("last_appid") or batch[-1]["appid"]


def fetch_review_counts(appids: list[int]) -> dict[int, int]:
    """Review counts via batched store items, 50 apps per call, resumable."""
    counts: dict[int, int] = {}
    batches = [appids[i : i + 500] for i in range(0, len(appids), 500)]
    for n, batch in enumerate(batches, 1):
        def produce(batch=batch):
            got = {}
            for attempt in range(3):
                try:
                    items = steam.store_items(batch)
                    break
                except UpstreamError as exc:  # pragma: no cover - network
                    print(f"    retrying after error: {exc}")
                    time.sleep(5 * (attempt + 1))
            else:
                items = {}
            for appid, item in items.items():
                if item:
                    got[str(appid)] = item["review_count"]
            time.sleep(0.5)
            return got
        part = _cached(f"reviews_{batch[0]}_{len(batch)}.json", produce)
        counts.update({int(k): v for k, v in part.items()})
        if n % 20 == 0 or n == len(batches):
            print(f"  review counts: {n}/{len(batches)} batches")
    return counts


def build_index() -> None:
    key = Config.STEAM_API_KEY
    if not key:
        raise SystemExit("STEAM_API_KEY is not set (see .env.example)")
    print("Search index")
    games = [g for g in fetch_games(key) if g.get("name") and not is_junk_name(g["name"])]
    print(f"  {len(games)} games after filtering")
    counts = fetch_review_counts([g["appid"] for g in games])
    rows = []
    for g in games:
        reviews = counts.get(g["appid"], 0)
        pop = round(math.log10(reviews + 1), 2)
        rows.append([g["appid"], g["name"], pop])
    rows.sort(key=lambda r: -r[2])
    _write_json(os.path.join(DATA, "search_index.json.gz"), rows, gz=True)


# ── Discovery pool ────────────────────────────────────────

STEAMSPY = "https://steamspy.com/api.php"
SEED_PATH = os.path.join(ROOT, "scripts", "seed_games.json")
MIN_REVIEWS = 300

# Steam category IDs → how a game can be played.
PLAYER_MODES = {
    "single": {2},
    "coop": {9, 38, 39, 48},
    "pvp": {36, 37, 47, 49},
    "multi": {1, 20, 27, 36, 38, 49},
}


def _pool_seed_ids() -> list[int]:
    ids: list[int] = []

    def add(many):
        for a in many:
            a = int(a)
            if a not in seen:
                seen.add(a)
                ids.append(a)

    seen: set[int] = set()
    with open(SEED_PATH, encoding="utf-8") as f:
        add(json.load(f))

    owned = _cached("steamspy_all_0.json", lambda: get_json(STEAMSPY, {"request": "all", "page": 0}, timeout=60))
    add(a for a, g in owned.items() if (g.get("positive", 0) + g.get("negative", 0)) >= MIN_REVIEWS)
    recent = _cached("steamspy_top2w.json", lambda: get_json(STEAMSPY, {"request": "top100in2weeks"}, timeout=60))
    add(recent)

    for chart in (steam.concurrent_chart, steam.most_played_chart):
        add(r["appid"] for r in (chart().get("ranks") or []))
    add(r["appid"] for r in (steam.weekly_top_sellers("US", 100).get("ranks") or []) if r.get("appid"))
    feat = steam.featured_categories("us")
    for key in ("new_releases", "top_sellers"):
        add(e["id"] for e in (feat.get(key) or {}).get("items") or [] if e.get("type", 0) == 0)
    return ids


_refusals = 0  # consecutive HTTP 429s; after a few we stop asking this run


def _hours(appid: int) -> dict | None:
    """Median playtime of a game's reviewers (one page of reviews).

    Steam rate-limits this endpoint hard, so: one request at a time, back off
    on HTTP 429, give up for the rest of the run if Steam keeps refusing, and
    only cache successes — rerunning later fills in the gaps.
    """
    global _refusals
    from services.reviews import _extract_hours

    path = os.path.join(CACHE, "hours", f"{appid}.json")
    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    if _refusals >= 3:
        return None
    fetch = steam.reviews_page.__wrapped__  # skip the in-process failure cache
    for attempt in range(2):
        try:
            reviews = fetch(appid, "all")
            _refusals = 0
            break
        except UpstreamError as exc:
            if "429" in str(exc):
                _refusals += 1
                if _refusals >= 3:
                    print("  Steam is rate-limiting review requests; skipping playtime for the rest of this run")
                    return None
                time.sleep(20)
            else:
                time.sleep(3)
    else:
        return None
    h = _extract_hours(reviews)
    out = {"p50_all": round(h["p50"], 1), "p50_recent": 0.0, "p75_recent": 0.0}
    with open(path, "w", encoding="utf-8") as f:
        json.dump(out, f)
    time.sleep(1.0)
    return out


def build_pool() -> None:
    print("Discovery pool")
    ids = _pool_seed_ids()
    print(f"  {len(ids)} candidate games")
    items: dict[int, dict] = {}
    for i in range(0, len(ids), 500):
        batch = ids[i : i + 500]
        part = _cached(f"pool_items_{batch[0]}_{len(batch)}.json",
                       lambda batch=batch: {str(k): v for k, v in steam.store_items(batch).items() if v})
        items.update({int(k): v for k, v in part.items()})

    keep = [
        items[a] for a in ids
        if a in items and items[a]["type"] == 0 and items[a]["name"] and items[a]["tags"]
        and (items[a]["review_count"] >= MIN_REVIEWS or items[a]["is_coming_soon"])
    ]
    print(f"  {len(keep)} games with tags and at least {MIN_REVIEWS} reviews; measuring playtime…")
    os.makedirs(os.path.join(CACHE, "hours"), exist_ok=True)
    hours = {}
    for n, g in enumerate(keep, 1):
        hours[g["appid"]] = _hours(g["appid"])
        if n % 50 == 0 or n == len(keep):
            print(f"  playtime: {n}/{len(keep)}", flush=True)
    missing = [a for a, h in hours.items() if h is None]
    if missing:
        print(f"  {len(missing)} games have no playtime yet; run this step again later to fill them in")

    games = []
    for it in keep:
        cats = set((it.get("categories") or {}).get("supported_player_categoryids") or [])
        games.append({
            "appid": it["appid"],
            "name": it["name"],
            "header": it["header"],
            "capsule": it["small_capsule"],
            "tags": it["tags"],
            "review_pct": it["review_pct"],
            "review_count": it["review_count"],
            "is_free": it["is_free"],
            "price": (it["price"] or {}).get("final"),
            "deck": it["deck"],
            "release": it["release_date"],
            "players": sorted(mode for mode, ids_ in PLAYER_MODES.items() if cats & ids_),
            "hours": hours[it["appid"]],
        })
    _write_json(os.path.join(DATA, "pool.json"), {"built": int(time.time()), "games": games})


STEPS = {"fx": build_fx, "tags": build_tags, "pool": build_pool, "index": build_index}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("step", choices=[*STEPS, "all"])
    args = parser.parse_args()
    for name, fn in STEPS.items():
        if args.step in (name, "all"):
            fn()


if __name__ == "__main__":
    main()
