"""Build the data snapshots PlayNexus ships with.

Run from the project root:

    python scripts/build_data.py fx      # exchange-rate fallback (no key)
    python scripts/build_data.py tags    # Steam tag names (no key)
    python scripts/build_data.py pool    # discovery pool with playtime (no key, ~15 min)
    python scripts/build_data.py index   # games-only search index (needs STEAM_API_KEY)
    python scripts/build_data.py gems    # indie gems for the home page (after index)
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
from services.http import UpstreamError, chunks, get_json  # noqa: E402
from services.search import is_junk_name, is_minor_dlc_name  # noqa: E402

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

def _app_list_page(key: str, last_appid: int, kind: str = "games") -> dict:
    return get_json(
        "https://api.steampowered.com/IStoreService/GetAppList/v1/",
        {
            "key": key,
            "include_games": "true" if kind == "games" else "false",
            "include_dlc": "true" if kind == "dlc" else "false",
            "include_software": "false",
            "include_videos": "false",
            "include_hardware": "false",
            "max_results": 50000,
            "last_appid": last_appid,
        },
        timeout=60,
    )


def fetch_apps(key: str, kind: str = "games") -> list[dict]:
    """Every app of one *kind* ("games" or "dlc") on the Steam store."""
    prefix = "applist" if kind == "games" else "applist_dlc"
    apps: list[dict] = []
    last = 0
    page = 0
    while True:
        resp = _cached(f"{prefix}_{last}.json", lambda: _app_list_page(key, last, kind)).get("response") or {}
        batch = resp.get("apps") or []
        apps.extend(batch)
        page += 1
        print(f"  {kind} list page {page}: {len(batch)} (total {len(apps)})", flush=True)
        if not resp.get("have_more_results") or not batch:
            return apps
        last = resp.get("last_appid") or batch[-1]["appid"]


def _items_raw(appids: list[int], data_request: dict) -> list[dict]:
    """Raw GetItems store items for up to 50 apps (raises on failure)."""
    payload = {
        "ids": [{"appid": a} for a in appids],
        "context": {"language": "english", "country_code": "US"},
        "data_request": data_request,
    }
    params = {"input_json": json.dumps(payload, separators=(",", ":"))}
    if Config.STEAM_API_KEY:
        params["key"] = Config.STEAM_API_KEY
    data = get_json(f"{steam.WEBAPI}/IStoreBrowseService/GetItems/v1/", params, timeout=30)
    return [
        item for item in ((data or {}).get("response") or {}).get("store_items") or []
        if item.get("success") == 1 and item.get("appid")
    ]


def _review_counts_raw(appids: list[int]) -> dict[int, int]:
    """Review counts for up to 50 apps straight from the API (raises on failure)."""
    return {
        int(item["appid"]): int(((item.get("reviews") or {}).get("summary_filtered") or {}).get("review_count") or 0)
        for item in _items_raw(appids, {"include_reviews": True})
    }


def _retrying(fetch_part):
    """Wrap a per-chunk fetch with backoff on HTTP 429; give up after six tries."""

    def run(part: list[int]) -> dict:
        for attempt in range(6):
            try:
                got = fetch_part(part)
                time.sleep(0.2)
                return got
            except UpstreamError as exc:
                wait = 30 * (attempt + 1) if "429" in str(exc) else 5
                print(f"    {exc}; waiting {wait}s", flush=True)
                time.sleep(wait)
        raise SystemExit("Steam keeps refusing. Progress is saved; run this step again later.")

    return run


def _fetch_in_batches(appids: list[int], prefix: str, fetch_part) -> dict[int, object]:
    """Run *fetch_part* on 50-app chunks (4 at a time), saving every 500 to
    .cache/ so a rerun resumes.

    Only successful batches are saved: a rate-limited response must never be
    recorded as "no data", or those games would be judged on nothing for good.
    """
    from concurrent.futures import ThreadPoolExecutor

    results: dict[int, object] = {}
    batches = [appids[i : i + 500] for i in range(0, len(appids), 500)]
    with ThreadPoolExecutor(max_workers=4) as pool:
        for n, batch in enumerate(batches, 1):
            path = os.path.join(CACHE, f"{prefix}_{batch[0]}_{len(batch)}.json")
            if os.path.exists(path):
                with open(path, encoding="utf-8") as f:
                    results.update({int(k): v for k, v in json.load(f).items()})
                continue
            got: dict = {}
            for part in pool.map(_retrying(fetch_part), list(chunks(batch, 50))):
                got.update(part)
            os.makedirs(CACHE, exist_ok=True)
            with open(path, "w", encoding="utf-8") as f:
                json.dump({str(k): v for k, v in got.items()}, f)
            results.update(got)
            if n % 10 == 0 or n == len(batches):
                print(f"  {prefix}: {n}/{len(batches)} batches", flush=True)
    return results


def fetch_review_counts(appids: list[int], prefix: str = "reviews") -> dict[int, int]:
    """Review counts, 50 apps per call, resumable (see :func:`_fetch_in_batches`)."""
    return _fetch_in_batches(appids, prefix, _review_counts_raw)


MIN_DLC_REVIEWS = 500  # expansions people actually bought and reviewed


def build_index() -> None:
    """Games, plus DLC that's a real expansion (not soundtracks or cosmetic
    packs) and that at least MIN_DLC_REVIEWS people have reviewed."""
    key = Config.STEAM_API_KEY
    if not key:
        raise SystemExit("STEAM_API_KEY is not set (see .env.example)")
    print("Search index")
    games = [g for g in fetch_apps(key, "games") if g.get("name") and not is_junk_name(g["name"])]
    print(f"  {len(games)} games after filtering", flush=True)
    counts = fetch_review_counts([g["appid"] for g in games], "reviews")

    dlc = [d for d in fetch_apps(key, "dlc") if d.get("name") and not is_minor_dlc_name(d["name"])]
    print(f"  {len(dlc)} DLC left after dropping soundtracks, cosmetics and packs", flush=True)
    dlc_counts = fetch_review_counts([d["appid"] for d in dlc], "dlcreviews")
    expansions = [d for d in dlc if dlc_counts.get(d["appid"], 0) >= MIN_DLC_REVIEWS]
    print(f"  {len(expansions)} DLC with at least {MIN_DLC_REVIEWS} reviews kept", flush=True)

    rows = [[g["appid"], g["name"], round(math.log10(counts.get(g["appid"], 0) + 1), 2), 0] for g in games]
    rows += [[d["appid"], d["name"], round(math.log10(dlc_counts[d["appid"]] + 1), 2), 1] for d in expansions]
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


# ── Indie gems (home page "Try …" suggestions) ────────────

INDIE_TAG = 492
GEM_MIN_PCT = 94
GEM_REVIEWS = (1_000, 70_000)  # loved, but not a megahit
ADULT_TAG_NAMES = {"Sexual Content", "Nudity", "NSFW", "Hentai"}


def _gem_fields(appids: list[int]) -> dict[int, dict]:
    """What the gem filter needs, for up to 50 apps (raises on failure)."""
    out: dict[int, dict] = {}
    request = {"include_reviews": True, "include_release": True, "include_tag_count": 10}
    for item in _items_raw(appids, request):
        reviews = (item.get("reviews") or {}).get("summary_filtered") or {}
        tags_ = sorted(item.get("tags") or [], key=lambda t: -t.get("weight", 0))
        out[int(item["appid"])] = {
            "name": item.get("name") or "",
            "type": item.get("type"),
            "free": bool(item.get("is_free")),
            "soon": bool(item.get("is_coming_soon")),
            "pct": reviews.get("percent_positive"),
            "count": int(reviews.get("review_count") or 0),
            "release": (item.get("release") or {}).get("steam_release_date"),
            "tags": [t["tagid"] for t in tags_],
        }
    return out


def build_gems() -> None:
    """Well-loved indie games that aren't megahits, for the home page."""
    print("Indie gems")
    index_path = os.path.join(DATA, "search_index.json.gz")
    if not os.path.exists(index_path):
        raise SystemExit("Build the search index first: python scripts/build_data.py index")
    with gzip.open(index_path, "rt", encoding="utf-8") as f:
        rows = json.load(f)
    # The index stores popularity as log10(reviews + 1), rounded; widen a hair.
    lo = math.log10(GEM_REVIEWS[0] + 1) - 0.01
    hi = math.log10(GEM_REVIEWS[1] + 1) + 0.01
    ids = [r[0] for r in rows if (len(r) < 4 or r[3] == 0) and lo <= r[2] <= hi]
    print(f"  {len(ids)} games with roughly {GEM_REVIEWS[0]:,}-{GEM_REVIEWS[1]:,} reviews", flush=True)

    adult = {tid for tid, name in steam_tag_names().items() if name in ADULT_TAG_NAMES}
    fields = _fetch_in_batches(ids, "gems", _gem_fields)
    now = time.time()
    gems = []
    for appid, f in fields.items():
        if (
            f["type"] == 0 and f["name"] and not f["free"] and not f["soon"]
            and f["release"] and f["release"] < now
            and (f["pct"] or 0) >= GEM_MIN_PCT
            and GEM_REVIEWS[0] <= f["count"] <= GEM_REVIEWS[1]
            and INDIE_TAG in f["tags"][:8]
            and not adult & set(f["tags"][:5])
        ):
            gems.append({"appid": appid, "name": f["name"], "review_pct": f["pct"],
                         "review_count": f["count"], "release": f["release"]})
    gems.sort(key=lambda g: (-g["review_pct"], -g["review_count"]))
    print(f"  {len(gems)} indie gems", flush=True)
    _write_json(os.path.join(DATA, "gems.json"), {"built": int(time.time()), "games": gems})


def steam_tag_names() -> dict[int, str]:
    """Tag names from the bundled snapshot (refresh with the tags step)."""
    with open(os.path.join(DATA, "tags.json"), encoding="utf-8") as f:
        return {int(k): v for k, v in json.load(f).items()}


STEPS = {"fx": build_fx, "tags": build_tags, "pool": build_pool, "index": build_index, "gems": build_gems}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("step", choices=[*STEPS, "all"])
    args = parser.parse_args()
    for name, fn in STEPS.items():
        if args.step in (name, "all"):
            fn()


if __name__ == "__main__":
    main()
