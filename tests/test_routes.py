import json
import re

import pytest

from conftest import make_game
from services import game as game_service
from services.http import UpstreamError


@pytest.fixture
def stub_game(monkeypatch):
    holder = {"game": make_game()}

    def load(appid):
        g = holder["game"]
        return None if g is None else {**g, "appid": appid}

    monkeypatch.setattr(game_service, "load_game", load)
    return holder


def test_game_page_is_cdn_cacheable(client, stub_game):
    r = client.get("/game/1086940")
    assert r.status_code == 200
    assert "s-maxage=1800" in r.headers["Cache-Control"]


def test_non_numeric_and_unknown_ids_404(client, stub_game):
    assert client.get("/game/elden-ring").status_code == 404
    stub_game["game"] = None
    assert client.get("/game/123").status_code == 404


def test_steam_outage_is_a_503(client, monkeypatch):
    def down(appid):
        raise UpstreamError("down")

    monkeypatch.setattr(game_service, "load_game", down)
    r = client.get("/game/1")
    assert r.status_code == 503
    assert b"Steam isn" in r.data


def test_hostile_names_are_escaped_everywhere(client, stub_game):
    evil = "Baldur's <script>alert(1)</script> \"Gate\""
    stub_game["game"] = make_game(name=evil)
    html = client.get("/game/1086940").get_data(as_text=True)
    assert "<script>alert(1)</script>" not in html
    # The JS copy of the name round-trips exactly through JSON.
    m = re.search(r'<script type="application/json" id="page-data">(.*?)</script>', html, re.S)
    assert m and json.loads(m.group(1))["name"] == evil


def test_search_form_resolves_names(client, monkeypatch):
    from routes import pages

    monkeypatch.setattr(pages, "find_appid_by_name", lambda q: 367520 if q == "hollow knight" else None)
    r = client.post("/game", data={"game_input": "hollow knight"})
    assert r.status_code == 302 and r.headers["Location"].endswith("/game/367520")
    r = client.post("/game", data={"game_input": "no such game"})
    assert r.status_code == 404


def test_api_search_is_cached_and_bounded(client, monkeypatch):
    from routes import api

    seen = {}

    def fake(q, limit):
        seen.update(q=q, limit=limit)
        return [{"appid": 1, "name": "x", "score": 1}]

    monkeypatch.setattr(api, "search", fake)
    r = client.get("/api/search?q=" + "a" * 200 + "&limit=999")
    assert r.status_code == 200 and "s-maxage=86400" in r.headers["Cache-Control"]
    assert len(seen["q"]) == 80 and seen["limit"] == 20


CHART_ROW = {
    "appid": 367520, "name": "Hollow Knight", "header": None, "capsule": None, "is_free": False,
    "price": {"final": 14.99, "initial": 14.99, "discount": 0, "final_formatted": "$14.99", "initial_formatted": ""},
    "review_pct": 97, "review_label": "Overwhelmingly Positive", "review_count": 400000,
    "tags": ["Metroidvania", "Souls-like"], "deck": 3, "release_date": 1487959251, "coming_soon": False,
    "rank": 1, "players": 12345, "peak": 23456, "last_week_rank": 4,
}


@pytest.fixture
def stub_charts(monkeypatch):
    from services import charts

    monkeypatch.setattr(charts, "safe", lambda fn, *a, **k: {"updated": 1792281751, "rows": [dict(CHART_ROW)]})


@pytest.mark.parametrize("tab", ["playing", "sellers", "new", "soon", "bogus"])
def test_chart_tabs_render(client, stub_charts, tab):
    r = client.get(f"/charts?tab={tab}")
    assert r.status_code == 200
    html = r.get_data(as_text=True)
    assert "Hollow Knight" in html and "$14.99" in html


def test_old_addresses_redirect(client):
    assert client.get("/trending").headers["Location"].endswith("/charts")
    assert client.get("/moods").headers["Location"].endswith("/discover")


def test_art_redirects_to_real_asset(client, monkeypatch):
    from services import steam

    monkeypatch.setattr(steam, "store_items", lambda ids, cc="US": {
        ids[0]: {"header": "https://cdn.example/h.jpg", "small_capsule": "https://cdn.example/c.jpg"}})
    r = client.get("/api/art/123?kind=capsule")
    assert r.status_code == 302 and r.headers["Location"] == "https://cdn.example/c.jpg"
    assert "s-maxage" in r.headers["Cache-Control"]

    monkeypatch.setattr(steam, "store_items", lambda ids, cc="US": {ids[0]: None})
    assert client.get("/api/art/123").status_code == 404


def test_home_renders_without_steam(client, monkeypatch):
    from services import charts

    monkeypatch.setattr(charts, "safe", lambda fn, *a, **k: {"updated": None, "rows": [], "error": True})
    r = client.get("/")
    assert r.status_code == 200 and b"Is it worth the money?" in r.data


def test_game_page_shows_value_prices_and_meta(client, stub_game):
    html = client.get("/game/1086940").get_data(as_text=True)
    assert "Excellent value" in html and "--score: 88" in html
    assert "₹ 1,999" in html and "Cheapest" in html
    assert "Steam Deck Verified" in html and "Larian Studios" in html
    assert "Becomes great value at" in html  # $59.99 is above the $41.50 target
    assert 'property="og:image" content="https://example.test/header.jpg"' in html


def test_game_page_free_and_unpriced_states(client, stub_game):
    from conftest import make_game

    stub_game["game"] = make_game(is_free=True, usd_price=0.0, prices={
        "is_free": True, "available": True, "regions": [], "us_usd": 0.0, "reference_usd": 0.0,
        "cheapest": None, "priciest": None, "fx": {"live": False, "source": "test", "updated": 0}})
    html = client.get("/game/730").get_data(as_text=True)
    assert "Free to play in every region." in html

    game = make_game()
    game["value"] = {**game["value"], "score": None, "label": "No price yet", "worth_it": None}
    game["prices"] = {**game["prices"], "available": False, "regions": []}
    game["coming_soon"] = True
    stub_game["game"] = game
    html = client.get("/game/1").get_data(as_text=True)
    assert "No price yet, so no value score" in html


def test_live_endpoint_finds_chart_rank(client, monkeypatch):
    from services import steam

    monkeypatch.setattr(steam, "current_players", lambda appid: 27343)
    monkeypatch.setattr(steam, "concurrent_chart", lambda: {"ranks": [
        {"rank": 59, "appid": 1245620, "concurrent_in_game": 27343, "peak_in_game": 40000}]})
    j = client.get("/api/game/1245620/live").get_json()
    assert j == {"players": 27343, "rank": 59, "peak_today": 40000}


def test_reviews_endpoint_shapes_history(client, monkeypatch):
    from services import steam

    monkeypatch.setattr(steam, "review_histogram", lambda appid: {
        "rollup_type": "month",
        "rollups": [{"date": 1, "recommendations_up": 10, "recommendations_down": 2}],
        "recent": [{"date": 2, "recommendations_up": 5, "recommendations_down": 1}] * 3,
    })
    j = client.get("/api/game/1/reviews").get_json()
    assert j["points"] == [{"t": 1, "up": 10, "down": 2}]
    assert j["recent"] == {"up": 15, "down": 3, "days": 3}


def test_whereami_maps_countries_and_is_private(client):
    r = client.get("/api/whereami", headers={"X-Vercel-IP-Country": "FR"})
    assert r.get_json() == {"country": "FR", "region": "EU"}
    assert r.headers["Cache-Control"] == "private, no-store"
    assert client.get("/api/whereami", headers={"X-Vercel-IP-Country": "IN"}).get_json()["region"] == "IN"
    assert client.get("/api/whereami", headers={"X-Vercel-IP-Country": "NG"}).get_json()["region"] is None
    assert client.get("/api/whereami").get_json() == {"country": None, "region": None}
