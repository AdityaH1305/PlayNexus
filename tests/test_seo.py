"""What search engines and analytics see: robots.txt, sitemap.xml, the game
page's JSON-LD and the Vercel Web Analytics tag."""

import json
import re
import urllib.robotparser
import xml.etree.ElementTree as ET
from datetime import datetime, timezone

import pytest

from config import Config
from conftest import make_game
from services import charts, pool
from services import game as game_service

SITE = "https://playnexus.example"
NS = {"sm": "http://www.sitemaps.org/schemas/sitemap/0.9"}
W3C_TIME = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z")
WEEK = 1792195200  # 17 Oct 2026, 00:00 UTC
NEW_GAME = 3999990  # a top seller that isn't in the pool
PAGES = ["/", "/charts", "/charts?tab=sellers", "/charts?tab=new", "/charts?tab=soon",
         "/deals", "/regions", "/discover", "/compare", "/me"]


@pytest.fixture
def site(monkeypatch):
    monkeypatch.setattr(Config, "SITE_URL", SITE)


@pytest.fixture
def sellers(monkeypatch):
    """The real data/pool.json, and this week's top sellers: one game from
    the pool and one that isn't. Returns the chart functions called."""
    pool.reset()
    calls = []

    def fake_safe(fn, *args, **kwargs):
        calls.append(fn)
        return {"updated": WEEK, "rows": [{"appid": pool.games()[0]["appid"]}, {"appid": NEW_GAME}]}

    monkeypatch.setattr(charts, "safe", fake_safe)
    return calls


def sitemap_entries(resp) -> list[tuple[str, str | None]]:
    root = ET.fromstring(resp.data)
    assert root.tag == f"{{{NS['sm']}}}urlset"
    return [(u.findtext("sm:loc", namespaces=NS), u.findtext("sm:lastmod", namespaces=NS)) for u in root]


def test_robots_blocks_only_the_api_and_names_the_sitemap(client, site):
    r = client.get("/robots.txt")
    assert r.status_code == 200 and r.mimetype == "text/plain"
    assert "s-maxage=86400" in r.headers["Cache-Control"]
    robots = urllib.robotparser.RobotFileParser()
    robots.parse(r.get_data(as_text=True).splitlines())
    assert not robots.can_fetch("*", f"{SITE}/api/search?q=portal")
    assert not robots.can_fetch("Googlebot", f"{SITE}/api/game/400")
    for path in ("/", "/game/400", "/charts?tab=sellers", "/sitemap.xml"):
        assert robots.can_fetch("*", SITE + path)
    assert robots.site_maps() == [f"{SITE}/sitemap.xml"]


def test_sitemap_lists_pages_pool_games_and_top_sellers(client, site, sellers):
    assert pool.get(NEW_GAME) is None
    r = client.get("/sitemap.xml")
    assert r.status_code == 200 and r.mimetype == "application/xml"
    assert "s-maxage=86400" in r.headers["Cache-Control"]
    assert r.data.startswith(b'<?xml version="1.0" encoding="UTF-8"?>')
    entries = sitemap_entries(r)
    lastmod = dict(entries)
    assert len(lastmod) == len(entries)  # no duplicates
    assert all(loc.startswith(SITE + "/") for loc in lastmod)

    assert {SITE + p for p in PAGES} <= lastmod.keys()
    # Live pages are dated; the static tools aren't.
    assert all(W3C_TIME.fullmatch(lastmod[SITE + p]) for p in PAGES if p not in ("/compare", "/me"))
    assert lastmod[SITE + "/compare"] is None and lastmod[SITE + "/me"] is None

    built = datetime.fromtimestamp(pool.built(), tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    games = {f"{SITE}/game/{g['appid']}" for g in pool.games()}
    assert len(games) > 500  # the real pool, not a test stub
    assert games <= lastmod.keys()
    assert {lastmod[loc] for loc in games} == {built}
    assert lastmod[f"{SITE}/game/{NEW_GAME}"] == "2026-10-17T00:00:00Z"
    assert len(entries) == len(PAGES) + len(games) + 1
    # One cached chart call for the whole sitemap, nothing per game.
    assert sellers == [charts.top_sellers]


def test_without_site_url_links_use_the_requested_host(client, sellers, monkeypatch):
    monkeypatch.setattr(Config, "SITE_URL", "")
    robots = client.get("/robots.txt", base_url="https://preview.example").get_data(as_text=True)
    assert "Sitemap: https://preview.example/sitemap.xml" in robots
    entries = sitemap_entries(client.get("/sitemap.xml", base_url="https://preview.example"))
    assert all(loc.startswith("https://preview.example/") for loc, _ in entries)


def test_sitemap_survives_a_steam_outage(client, site, monkeypatch):
    pool.reset()
    monkeypatch.setattr(charts, "safe", lambda fn, *a, **k: {"updated": None, "rows": [], "error": True})
    r = client.get("/sitemap.xml")
    assert r.status_code == 200
    assert "s-maxage=60" in r.headers["Cache-Control"]  # retried soon, not kept for a day
    locs = [loc for loc, _ in sitemap_entries(r)]
    assert f"{SITE}/game/{pool.games()[0]['appid']}" in locs


def test_chart_tabs_are_their_own_canonical_pages(client, monkeypatch):
    from app import app

    monkeypatch.setitem(app.jinja_env.globals, "site_url", SITE)
    monkeypatch.setattr(charts, "safe", lambda fn, *a, **k: {"updated": None, "rows": []})
    html = client.get("/charts?tab=sellers").get_data(as_text=True)
    assert f'<link rel="canonical" href="{SITE}/charts?tab=sellers">' in html
    assert f'<meta property="og:url" content="{SITE}/charts?tab=sellers">' in html
    for path in ("/charts", "/charts?tab=playing", "/charts?tab=bogus"):
        assert f'<link rel="canonical" href="{SITE}/charts">' in client.get(path).get_data(as_text=True)


def json_ld(html: str) -> dict:
    head = html.split("</head>", 1)[0]
    found = re.findall(r'<script type="application/ld\+json">(.*?)</script>', head, re.S)
    assert len(found) == 1, "expected one JSON-LD block in <head>"
    return json.loads(found[0])


def test_game_page_describes_the_game_as_json_ld(client, site, monkeypatch):
    monkeypatch.setattr(game_service, "load_game", lambda appid: make_game(appid=appid))
    data = json_ld(client.get("/game/1086940").get_data(as_text=True))
    assert data == {
        "@context": "https://schema.org",
        "@type": "VideoGame",
        "name": "Baldur's Gate 3",
        "url": f"{SITE}/game/1086940",
        "image": "https://example.test/header.jpg",
        "genre": ["RPG"],
        "gamePlatform": ["Windows", "macOS"],
        "publisher": [{"@type": "Organization", "name": "Larian Studios"}],
        "datePublished": "2023-08-03",
    }
    # Steam's reviews aren't ours to mark up as a rating (Google's rules).
    assert "aggregateRating" not in data


def test_json_ld_is_escaped_and_leaves_out_what_it_doesnt_know(client, site, monkeypatch):
    evil = "Gate </script><script>alert(1)</script>"
    meta = {**make_game()["meta"], "release": "Q1 2027", "coming_soon": True,
            "genres": [], "platforms": [], "publishers": []}
    monkeypatch.setattr(game_service, "load_game",
                        lambda appid: make_game(appid=appid, name=evil, header_image=None, meta=meta))
    html = client.get("/game/1").get_data(as_text=True)
    assert "</script><script>alert(1)" not in html
    assert json_ld(html) == {"@context": "https://schema.org", "@type": "VideoGame", "name": evil, "url": f"{SITE}/game/1"}


@pytest.mark.parametrize("text, iso", [
    ("Aug 3, 2023", "2023-08-03"),
    ("3 Aug, 2023", "2023-08-03"),
    ("December 25, 2026", "2026-12-25"),
    ("Q1 2027", None),
    ("Coming soon", None),
    ("", None),
])
def test_release_dates_become_iso_dates(text, iso):
    assert game_service.release_iso(text) == iso


def test_unreleased_games_have_no_publish_date():
    meta = {**make_game()["meta"], "release": "Nov 14, 2026", "coming_soon": True}
    assert "datePublished" not in game_service.structured_data(make_game(meta=meta), f"{SITE}/game/1")


def test_analytics_script_only_on_vercel(client, monkeypatch):
    from app import app

    tag = b'<script defer src="/_vercel/insights/script.js"></script>'
    monkeypatch.setitem(app.jinja_env.globals, "vercel_analytics", False)
    assert tag not in client.get("/compare").data
    monkeypatch.setitem(app.jinja_env.globals, "vercel_analytics", True)
    assert tag in client.get("/compare").data
