"""services.game.load_game when Steam's review endpoints are rate-limited."""

import pytest

from conftest import make_game
from services import game as game_service
from services import pool, pricing, reviews, steam

EMPTY_STATS = {
    "available": False, "positivity": 0.0, "total_reviews": 0, "total_positive": 0, "total_negative": 0,
    "review_label": "", "p50_all": 0.0, "p50_recent": 0.0, "p75_recent": 0.0, "recent_positivity": 0.0,
    "recent_count": 0,
}
PRICES = {
    "is_free": False, "available": True, "regions": [], "us_usd": 29.99, "reference_usd": 29.99,
    "cheapest": None, "priciest": None, "fx": {"live": False, "source": "test", "updated": 0},
}
ITEM = {
    "appid": 1, "name": "Test Game", "type": 0, "header": "h", "hero": None, "tags": [], "deck": 3,
    "review_count": 40000, "review_pct": 92, "review_label": "Very Positive",
}


@pytest.fixture
def steam_down(monkeypatch):
    monkeypatch.setattr(steam, "appdetails", lambda appid, cc="us": {"name": "Test Game", "is_free": False})
    monkeypatch.setattr(steam, "store_items", lambda ids, cc="US": {1: dict(ITEM)})
    monkeypatch.setattr(pricing, "submit_regional", lambda appid: {})
    monkeypatch.setattr(pricing, "assemble_regional", lambda futures, is_free=False: dict(PRICES))
    monkeypatch.setattr(reviews, "submit_review_calls", lambda appid: None)
    monkeypatch.setattr(reviews, "assemble_review_stats", lambda futures: dict(EMPTY_STATS))


def test_failed_review_lookup_falls_back_to_store_numbers(steam_down, monkeypatch):
    monkeypatch.setattr(pool, "get", lambda appid: None)
    g = game_service.load_game(1)
    assert g["degraded"] is True
    assert g["reviews"]["available"] and g["reviews"]["total_reviews"] == 40000
    # No playtime anywhere: say so instead of inventing a low score.
    assert g["value"]["score"] is None and g["value"]["label"] == "Not enough data"


def test_pool_playtime_fills_the_gap(steam_down, monkeypatch):
    known = {"hours": {"p50_all": 30.0, "p50_recent": 0.0, "p75_recent": 0.0}}
    monkeypatch.setattr(pool, "get", lambda appid: known)
    monkeypatch.setattr(pool, "has_hours", lambda g: True)
    g = game_service.load_game(1)
    assert g["value"]["score"] is not None
    assert g["value"]["breakdown"]["effective_hours"] == 30.0


def test_dlc_pages_link_the_base_game_and_skip_the_value_score(steam_down, monkeypatch):
    monkeypatch.setattr(steam, "appdetails", lambda appid, cc="us": {
        "name": "ELDEN RING Shadow of the Erdtree", "is_free": False, "type": "dlc",
        "fullgame": {"appid": "1245620", "name": "ELDEN RING"}})
    known = {"hours": {"p50_all": 300.0, "p50_recent": 0.0, "p75_recent": 0.0}}
    monkeypatch.setattr(pool, "get", lambda appid: known)
    monkeypatch.setattr(pool, "has_hours", lambda g: True)
    g = game_service.load_game(2778580)
    assert g["is_dlc"] and g["base_game"] == {"appid": 1245620, "name": "ELDEN RING"}
    assert g["value"]["score"] is None and g["value"]["label"] == "DLC"
    assert g["value"]["worth_it"] is None and g["value"]["by_region"] == {}


def test_dlc_page_renders_base_game_link(client, monkeypatch):
    monkeypatch.setattr(game_service, "load_game", lambda appid: make_game(
        is_dlc=True, base_game={"appid": 1245620, "name": "ELDEN RING"},
        value={**make_game()["value"], "score": None, "label": "DLC", "worth_it": None, "by_region": {}}))
    html = client.get("/game/2778580").get_data(as_text=True)
    assert 'href="/game/1245620"' in html and "DLC" in html
    assert "Value scores are for full games" in html


def test_degraded_pages_are_cached_briefly(client, monkeypatch):
    monkeypatch.setattr(game_service, "load_game", lambda appid: make_game(degraded=True))
    r = client.get("/game/1086940")
    assert r.status_code == 200
    assert "s-maxage=60" in r.headers["Cache-Control"] and "stale-while-revalidate=0" in r.headers["Cache-Control"]
