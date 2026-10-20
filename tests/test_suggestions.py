import json
import random
import re
import time
from collections import Counter

import pytest

from services import pool, steam, suggestions
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
FAVORITES = [{"appid": 200 + i, "name": f"Favorite {i}", "review_pct": 96} for i in range(8)]


def down(*a, **k):
    raise UpstreamError("down")


@pytest.fixture
def live(monkeypatch):
    by_id = {g["appid"]: g for g in TOP_SELLERS}
    monkeypatch.setattr(steam, "weekly_top_sellers", lambda cc="US", count=50: {
        "ranks": [{"rank": i + 1, "appid": g["appid"]} for i, g in enumerate(TOP_SELLERS)]})
    monkeypatch.setattr(steam, "featured_categories", lambda cc="us": {"new_releases": {"items": []}})
    monkeypatch.setattr(steam, "store_items", lambda ids, cc="US": {a: by_id.get(a) for a in ids})
    monkeypatch.setattr(suggestions, "_gems", lambda: GEMS)
    monkeypatch.setattr(suggestions, "_favorites", lambda: FAVORITES)
    monkeypatch.setattr(suggestions, "_adult_tags", lambda: {ADULT})


def kinds(sets):
    """How many picks of each kind, by the start of their tooltips."""
    return Counter(p["why"].split(",")[0] for s in sets for p in s)


def test_live_candidates_split_popular_indies_from_big_hits(live):
    c = suggestions._live_candidates()
    assert [g["name"] for g in c["indies"]] == ["Popular Indie A", "Popular Indie B"]
    # Best reviewed first. An indie that big is a hit, not a niche pick; unreleased,
    # free, adult and old games and DLC are out.
    assert [g["name"] for g in c["hits"]] == ["Another Hit", "Megahit Indie", "Big Release"]


def test_sets_differ_and_hold_three_different_games(live):
    sets = suggestions.pick_sets(rng=random.Random(1))
    assert len(sets) == suggestions.SETS
    assert all(len({p["appid"] for p in s}) == 3 for s in sets)
    assert len({frozenset(p["appid"] for p in s) for s in sets}) == len(sets)


def test_mostly_indies_sometimes_popular_games(live):
    sets = suggestions.pick_sets(count=60, rng=random.Random(2))
    k = kinds(sets)
    popular = k["A big recent release"] + k["A fan favorite"]
    assert 0.15 < popular / sum(k.values()) < 0.45
    assert k["A big recent release"] and k["A fan favorite"] and k["Popular indie this week"]
    assert k["Indie gem"] > popular

    with_popular = [any(p["why"].startswith(("A big", "A fan")) for p in s) for s in sets]
    assert any(with_popular) and not all(with_popular)  # some sets are all indie

    source = {
        "Popular indie this week": {"Popular Indie A", "Popular Indie B"},
        "Indie gem": {g["name"] for g in GEMS},
        "A big recent release": {"Another Hit", "Megahit Indie", "Big Release"},
        "A fan favorite": {g["name"] for g in FAVORITES},
    }
    assert all(p["name"] in source[p["why"].split(",")[0]] for s in sets for p in s)


def test_this_weeks_short_lists_appear_at_most_once_per_set(live):
    for s in suggestions.pick_sets(count=60, rng=random.Random(5)):
        k = kinds([s])
        assert k["Popular indie this week"] <= 1 and k["A big recent release"] <= 1


def test_names_read_well_in_a_sentence(live, monkeypatch):
    monkeypatch.setattr(suggestions, "_gems", lambda: [
        {**GEMS[0], "name": "沉睡的法则 Things as They Are"},
        {**GEMS[1], "name": "Milk outside a bag of milk outside a bag of milk"},
        {**GEMS[2], "name": "Sudoku Universe"},
    ])
    monkeypatch.setattr(suggestions, "_favorites", lambda: [{"appid": 300, "name": "DARK SOULS™ III", "review_pct": 94}])
    names = {p["name"] for s in suggestions.pick_sets(rng=random.Random(6)) for p in s}
    assert {"DARK SOULS III", "Sudoku Universe"} <= names
    assert not any("法则" in n or "Milk" in n for n in names)


def test_gems_and_favorites_carry_on_when_steam_is_down(live, monkeypatch):
    monkeypatch.setattr(steam, "weekly_top_sellers", down)
    sets = suggestions.pick_sets(rng=random.Random(3))
    assert len(sets) == suggestions.SETS
    assert set(kinds(sets)) == {"Indie gem", "A fan favorite"}


def test_short_lists_give_fewer_sets_rather_than_repeats(live, monkeypatch):
    monkeypatch.setattr(steam, "weekly_top_sellers", down)
    monkeypatch.setattr(suggestions, "_gems", lambda: GEMS[:2])
    monkeypatch.setattr(suggestions, "_favorites", lambda: [])
    sets = suggestions.pick_sets(rng=random.Random(4))
    assert len(sets) == 1 and {p["name"] for p in sets[0]} == {"Gem 0", "Gem 1"}


def test_last_resort_is_the_classic_trio(live, monkeypatch):
    monkeypatch.setattr(steam, "weekly_top_sellers", down)
    monkeypatch.setattr(suggestions, "_gems", lambda: [])
    monkeypatch.setattr(suggestions, "_favorites", lambda: [])
    assert suggestions.pick_sets() == [suggestions.FALLBACK]


def pool_game(appid, name, *, pct=96, count=4000, free=False, price=9.99):
    return {"appid": appid, "name": name, "tags": [[INDIE, 900]], "review_pct": pct, "review_count": count,
            "is_free": free, "price": None if free else price, "deck": 3, "release": NOW,
            "players": ["single"], "hours": None}


@pytest.fixture
def fake_pool():
    def use(games):
        pool.reset(pool.build({"built": 1, "games": games}, {INDIE: "Indie", ACTION: "Action"}))

    yield use
    pool.reset()


def test_gem_fallback_uses_the_pool(monkeypatch, tmp_path, fake_pool):
    monkeypatch.setattr(suggestions, "GEMS_PATH", str(tmp_path / "missing.json"))
    suggestions._gems.cache_clear()
    fake_pool([pool_game(1, "Pool Gem"), pool_game(2, "Pool Megahit", count=900_000)])
    try:
        assert [g["name"] for g in suggestions._gems()] == ["Pool Gem"]
    finally:
        suggestions._gems.cache_clear()


def test_favorites_are_huge_well_loved_paid_games(fake_pool):
    fake_pool([
        pool_game(1, "Loved", count=300_000),
        pool_game(2, "Loved But Free", count=900_000, free=True),
        pool_game(3, "Big But Mixed", count=300_000, pct=80),
        pool_game(4, "Small Gem", count=20_000),
        pool_game(5, "No Longer Sold", count=300_000, price=None),
    ])
    assert [g["name"] for g in suggestions._favorites()] == ["Loved"]


# ── The home page ───────────────────────────────────────────

A, B, C, D = ({"appid": i, "name": name, "why": f"why {name}"} for i, name in enumerate("ABCD", start=1))


def _try_line(page):
    line = re.search(r'<p class="hero__try"[^>]*>(.*?)</p>', page, re.S).group(1)
    return " ".join(re.sub(r"<[^>]+>", " ", line).split())


@pytest.fixture
def home(client, monkeypatch):
    """Fetches the home page with ``suggestions.pick_sets`` swapped for *picker*."""
    from services import charts, deals

    monkeypatch.setattr(charts, "safe", lambda fn, *a, **k: {"updated": None, "rows": []})
    monkeypatch.setattr(deals, "current_deals", lambda *a, **k: [])

    def get(picker):
        monkeypatch.setattr(suggestions, "pick_sets", picker)
        return client.get("/").get_data(as_text=True)

    return get


def test_home_page_shows_the_first_set_and_carries_them_all(home):
    sets = [[A, B, C], [D, A, B]]
    page = home(lambda: sets)
    assert _try_line(page) == "Try A , B or C ."
    assert 'href="/game/1" title="why A"' in page
    data = re.search(r'<script type="application/json" id="try-sets">(.*?)</script>', page, re.S).group(1)
    assert json.loads(data) == sets
    assert 'sessionStorage.getItem("playnexus_try")' in page  # the script that picks one


def test_a_single_set_of_two_needs_no_script(home):
    page = home(lambda: [[A, B]])
    assert _try_line(page) == "Try A or B ."
    assert 'id="try-sets"' not in page


def test_a_name_ending_in_a_full_stop_ends_the_sentence(home):
    page = home(lambda: [[A, {"appid": 9, "name": "R.E.P.O.", "why": "x"}]])
    assert _try_line(page) == "Try A or R.E.P.O."


def test_home_page_survives_a_broken_picker(home):
    def broken():
        raise RuntimeError("bug")

    assert "Hades" in _try_line(home(broken))
