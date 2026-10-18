"""Build the data snapshots PlayNexus ships with.

Run from the project root:

    python scripts/build_data.py fx      # exchange-rate fallback (no key)
    python scripts/build_data.py tags    # Steam tag names (no key)
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


STEPS = {"fx": build_fx, "tags": build_tags, "index": build_index}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("step", choices=[*STEPS, "all"])
    args = parser.parse_args()
    for name, fn in STEPS.items():
        if args.step in (name, "all"):
            fn()


if __name__ == "__main__":
    main()
