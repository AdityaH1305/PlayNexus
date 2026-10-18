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
    m = re.search(r"const GAME = (\{.*?\});", html)
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
