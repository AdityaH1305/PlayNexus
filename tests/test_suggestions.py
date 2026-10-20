import datetime as dt
import re
import time

import pytest

from services import steam, suggestions
from services.http import UpstreamError

DAY = 86400
NOW = time.time()
INDIE, ACTION, ADULT = 492, 19, 12095


def item(appid, name, *, pct=95, count=5000, age_days=60, tags=(INDIE, ACTION), free=False, soon=False, kind=0):
    return {
        "appid": appid, "name": name, "type": kind, "is_free": free, "is_coming_soon": soon,
        "release_date": NOW - age_days * DAY, "tags": [(t, 1000 - i) for i, t in enumerate(tags)],
        "review_pct": pct, "review_count": count,
    }


TOP_SELLERS = [
    item(1, "Popular Indie A", count=20000),
    item(2, "Popular Indie B", count=3000),
    item(3, "Megahit Indie", count=400_000),                       # too big to count as niche
    item(4, "Big Release", tags=(ACTION,), count=40_000, age_days=90),
    item(5, "Unreleased Shooter", tags=(ACTION,), count=9000, age_days=-3, soon=True),
    item(6, "Free Indie", free=True),
    item(7, "Adult Indie", tags=(ADULT, INDIE)),
    item(8, "Old Classic", tags=(ACTION,), count=90_000, age_days=2000),  # not recent
    item(9, "Another Hit", tags=(ACTION,), count=12_000, age_days=200, pct=97),
    item(10, "Some DLC", kind=4),
]
GEMS = [{"appid": 100 + i, "name": f"Gem {i}", "review_pct": 95, "review_count": 3000,
         "release": NOW - (i * 200) * DAY} for i in range(12)]


@pytest.fixture
def live(monkeypatch):
    by_id = {g["appid"]: g for g in TOP_SELLERS}
    monkeypatch.setattr(steam, "weekly_top_sellers", lambda cc="US", count=50: {
        "ranks": [{"rank": i + 1, "appid": g["appid"]} for i, g in enumerate(TOP_SELLERS)]})
    monkeypatch.setattr(steam, "featured_categories", lambda cc="us": {"new_releases": {"items": []}})
    monkeypatch.setattr(steam, "store_items", lambda ids, cc="US": {a: by_id.get(a) for a in ids})
    monkeypatch.setattr(suggestions, "_gems", lambda: GEMS)
    monkeypatch.setattr(suggestions, "_adult_tags", lambda: {ADULT})


_BASE = dt.date(2026, 10, 20)
EVEN = _BASE if _BASE.toordinal() % 2 == 0 else _BASE + dt.timedelta(days=1)  # a "big hit" day
ODD = EVEN + dt.timedelta(days=1)


def test_live_candidates_filter_out_megahits_free_adult_unreleased_and_dlc(live):
    c = suggestions._live_candidates()
    assert [g["name"] for g in c["indies"]] == ["Popular Indie A", "Popular Indie B"]
    assert [g["name"] for g in c["hits"]] == ["Another Hit", "Big Release"]  # best reviewed first


def test_picks_are_stable_for_a_day_and_change_over_a_week(live):
    assert EVEN.toordinal() % 2 == 0
    assert suggestions.todays_picks(EVEN) == suggestions.todays_picks(EVEN)
    week = {tuple(p["appid"] for p in suggestions.todays_picks(EVEN + dt.timedelta(days=d))) for d in range(7)}
    assert len(week) > 3


def test_slots_trending_indie_gem_and_alternating_hit(live):
    even = suggestions.todays_picks(EVEN)
    assert len(even) == 3 and len({p["appid"] for p in even}) == 3
    assert even[0]["why"] == "Popular indie this week" and even[0]["name"] in ("Popular Indie A", "Popular Indie B")
    assert even[1]["why"].startswith("Indie gem")
    assert even[2]["why"].startswith("A big recent release") and even[2]["name"] in ("Another Hit", "Big Release")

    odd = suggestions.todays_picks(ODD)
    assert [p["why"].split(",")[0] for p in odd[1:]] == ["Indie gem", "Indie gem"]


def test_gems_fill_in_when_steam_is_down(live, monkeypatch):
    def down(*a, **k):
        raise UpstreamError("down")

    monkeypatch.setattr(steam, "weekly_top_sellers", down)
    picks = suggestions.todays_picks(EVEN)
    assert len(picks) == 3 and all(p["why"].startswith("Indie gem") for p in picks)


def test_last_resort_is_the_classic_trio(live, monkeypatch):
    def down(*a, **k):
        raise UpstreamError("down")

    monkeypatch.setattr(steam, "weekly_top_sellers", down)
    monkeypatch.setattr(suggestions, "_gems", lambda: [])
    assert suggestions.todays_picks(EVEN) == suggestions.FALLBACK


def test_gem_fallback_uses_the_pool(monkeypatch, tmp_path):
    from services import pool

    monkeypatch.setattr(suggestions, "GEMS_PATH", str(tmp_path / "missing.json"))
    suggestions._gems.cache_clear()
    names = {1: "Indie", 2: "Action"}
    games = [
        {"appid": 1, "name": "Pool Gem", "tags": [[INDIE, 900]], "review_pct": 96, "review_count": 4000,
         "is_free": False, "price": 9.99, "deck": 3, "release": NOW, "players": ["single"], "hours": None},
        {"appid": 2, "name": "Pool Megahit", "tags": [[INDIE, 900]], "review_pct": 96, "review_count": 900_000,
         "is_free": False, "price": 9.99, "deck": 3, "release": NOW, "players": ["single"], "hours": None},
    ]
    pool.reset(pool.build({"built": 1, "games": games}, names))
    try:
        assert [g["name"] for g in suggestions._gems()] == ["Pool Gem"]
    finally:
        pool.reset()
        suggestions._gems.cache_clear()


def _try_line(html):
    return " ".join(re.sub(r"<[^>]+>", " ", re.search(r'<p class="hero__try">(.*?)</p>', html, re.S).group(1)).split())


@pytest.mark.parametrize("picks,expected", [
    ([{"appid": 1, "name": "A", "why": "x"}, {"appid": 2, "name": "B", "why": "y"}, {"appid": 3, "name": "C", "why": "z"}],
     "Try A , B or C ."),
    ([{"appid": 1, "name": "A", "why": "x"}, {"appid": 2, "name": "B", "why": "y"}], "Try A or B ."),
])
def test_home_page_sentence(client, monkeypatch, picks, expected):
    from services import charts, deals

    monkeypatch.setattr(charts, "safe", lambda fn, *a, **k: {"updated": None, "rows": []})
    monkeypatch.setattr(deals, "current_deals", lambda *a, **k: [])
    monkeypatch.setattr(suggestions, "todays_picks", lambda: picks)
    html = client.get("/").get_data(as_text=True)
    assert _try_line(html) == expected
    assert 'href="/game/1" title="x"' in html


def test_home_page_survives_a_broken_picker(client, monkeypatch):
    from services import charts, deals

    def broken():
        raise RuntimeError("bug")

    monkeypatch.setattr(charts, "safe", lambda fn, *a, **k: {"updated": None, "rows": []})
    monkeypatch.setattr(deals, "current_deals", lambda *a, **k: [])
    monkeypatch.setattr(suggestions, "todays_picks", broken)
    html = client.get("/").get_data(as_text=True)
    assert "Hades" in _try_line(html)
