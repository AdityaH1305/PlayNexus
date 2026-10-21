"""IsThereAnyDeal's price log, Steam's sale ends and calendar, and the price chart built on them."""

import datetime as dt
import json
from urllib.parse import parse_qs, urlparse

import pytest
import responses

from config import Config
from services import http, itad, steam, steam_sales, timing
from services.http import UpstreamError

D = dt.date
LOOKUP = "https://api.isthereanydeal.com/games/lookup/v1"
HISTORY = "https://api.isthereanydeal.com/games/history/v2"
GET_ITEMS = "https://api.steampowered.com/IStoreBrowseService/GetItems/v1/"


@pytest.fixture
def key(monkeypatch):
    monkeypatch.setattr(Config, "ITAD_API_KEY", "test-key")
    monkeypatch.setattr(http.time, "sleep", lambda s: None)


def deal(cents, regular=5999):
    return {"price": {"amount": cents / 100, "amountInt": cents, "currency": "USD"},
            "regular": {"amount": regular / 100, "amountInt": regular, "currency": "USD"}, "cut": 0}


# ── ITAD ────────────────────────────────────────────────────

def test_off_without_a_key():
    assert not itad.enabled()
    with pytest.raises(UpstreamError):
        itad.steam_history(1)


@responses.activate
def test_the_log_comes_back_oldest_first_on_steam_days(key):
    responses.add(responses.GET, LOOKUP, json={"found": True, "game": {"id": "uuid-1", "slug": "test-game"}})
    responses.add(responses.GET, HISTORY, json=[
        {"timestamp": "2026-10-08T19:30:00+02:00", "shop": {"id": 61}, "deal": deal(5999)},
        {"timestamp": "2026-10-01T17:02:11+00:00", "shop": {"id": 61}, "deal": deal(2399)},
        {"timestamp": "2026-09-01T03:10:00+00:00", "shop": {"id": 61}, "deal": None},
    ])
    log = itad.steam_history(1)
    assert log["url"] == "https://isthereanydeal.com/game/test-game/"
    assert log["events"] == [
        {"day": D(2026, 8, 31), "price": None, "regular": None},  # 03:10 UTC is the evening before in Pacific
        {"day": D(2026, 10, 1), "price": 2399, "regular": 5999},
        {"day": D(2026, 10, 8), "price": 5999, "regular": 5999},
    ]
    query = parse_qs(urlparse(responses.calls[1].request.url).query)
    assert (query["id"], query["shops"], query["country"], query["since"]) == (["uuid-1"], ["61"], ["US"], [itad.SINCE])


@responses.activate
def test_unknown_games_and_refusals(key):
    responses.add(responses.GET, LOOKUP, json={"found": False})
    assert itad.steam_history(1) is None
    responses.replace(responses.GET, LOOKUP, status=403)
    with pytest.raises(UpstreamError):
        itad.steam_history(2)
    responses.replace(responses.GET, LOOKUP, status=429)
    with pytest.raises(UpstreamError):
        itad.steam_history(3)


def test_other_currencies_are_skipped():
    euro = {"price": {"amountInt": 1999, "currency": "EUR"}, "regular": {"amountInt": 2999, "currency": "EUR"}}
    assert itad.parse_log([{"timestamp": "2026-01-01T18:00:00Z", "deal": euro}, {"timestamp": "nonsense"}]) == []


# ── Steam: when a sale ends ─────────────────────────────────

@responses.activate
def test_a_sale_ends_when_the_games_own_package_says():
    responses.add(responses.GET, GET_ITEMS, json={"response": {"store_items": [{"appid": 5, "purchase_options": [
        {"bundleid": 9, "active_discounts": [{"discount_end_date": 1700000000}]},
        {"packageid": 111, "active_discounts": [{"discount_end_date": 1793900000}, {"discount_end_date": 1793728800}]},
        {"packageid": 222, "active_discounts": [{"discount_end_date": 1790000000}]},
    ]}]}})
    assert steam.discount_end(5, 111) == 1793728800  # the earliest of its own discounts, not the bundle's
    assert steam.discount_end(5, 999) == 1793728800  # unknown package: its first package
    payload = json.loads(parse_qs(urlparse(responses.calls[0].request.url).query)["input_json"][0])
    assert payload["data_request"] == {"include_all_purchase_options": True}


@responses.activate
def test_no_discount_no_end():
    responses.add(responses.GET, GET_ITEMS, json={"response": {"store_items": [
        {"appid": 5, "purchase_options": [{"packageid": 111}]}]}})
    assert steam.discount_end(5, 111) is None


# ── The sale calendar ───────────────────────────────────────

def test_the_calendar_is_tidy():
    sales = steam_sales.sales()
    assert {s["name"] for s in sales} == {"Spring Sale", "Summer Sale", "Autumn Sale", "Winter Sale"}
    assert all(7 <= (s["end"] - s["start"]).days <= 19 for s in sales)
    assert all(a["end"] < b["start"] for a, b in zip(sales, sales[1:]))  # in order, no overlaps


def test_next_and_running_sales():
    assert steam_sales.label(steam_sales.upcoming(D(2026, 10, 21))) == "Winter Sale 2026"
    # A cooldown that ends partway through a sale still lets the game join it.
    assert steam_sales.upcoming(D(2026, 10, 21), D(2027, 1, 3))["name"] == "Winter Sale"
    assert steam_sales.upcoming(D(2026, 10, 21), D(2027, 1, 4))["name"] == "Spring Sale"
    assert steam_sales.running(D(2026, 10, 3))["name"] == "Autumn Sale"
    assert steam_sales.running(D(2026, 10, 8)) is None  # it ended at 10 a.m. that morning
    assert steam_sales.label(steam_sales.past(D(2026, 10, 21))[0]) == "Autumn Sale 2026"


# ── The price chart from ITAD ───────────────────────────────

TODAY = D(2026, 10, 21)


@pytest.fixture
def itad_log(monkeypatch):
    monkeypatch.setattr(Config, "ITAD_API_KEY", "test-key")
    monkeypatch.setattr(timing, "steam_today", lambda: TODAY)
    monkeypatch.setattr(steam, "price_overview", lambda ids, cc, failed=None: {
        ids[0]: {"currency": "USD", "initial": 5999, "final": 5999, "discount_percent": 0}})
    events = [{"day": D(2024, 1, 1), "price": 5999, "regular": 5999},
              {"day": D(2026, 10, 1), "price": 2399, "regular": 5999},
              {"day": D(2026, 10, 8), "price": 5999, "regular": 5999}]
    monkeypatch.setattr(itad, "steam_history", lambda appid: {"events": events, "url": "https://isthereanydeal.com/game/x/"})


def test_the_chart_shows_two_years_from_itad(client, itad_log):
    h = client.get("/api/game/1/history").get_json()
    start, window = TODAY - dt.timedelta(days=729), TODAY - dt.timedelta(days=89)
    assert [(p["day"], p["final"]) for p in h["points"]] == [
        (start.isoformat(), 5999), (window.isoformat(), 5999),
        ("2026-10-01", 2399), ("2026-10-08", 5999), ("2026-10-21", 5999),
    ]
    assert h["points"][2]["discount"] == 60
    assert h["low"] == {"final": 2399, "day": "2026-10-01", "full": True, "since": window.isoformat()}
    assert h["status"] is None and h["now"] == 5999
    assert h["source"] == {"name": "IsThereAnyDeal", "url": "https://isthereanydeal.com/game/x/"}
    assert h["sales"][-1] == {"name": "Autumn Sale", "start": "2026-10-01", "end": "2026-10-08"}
    assert h["sales"][0]["start"] >= start.isoformat() or h["sales"][0]["end"] > start.isoformat()


def test_the_chart_falls_back_and_is_cached_briefly_when_itad_fails(client, itad_log, monkeypatch):
    def busy(appid):
        raise UpstreamError("ITAD is busy")

    monkeypatch.setattr(itad, "steam_history", busy)
    r = client.get("/api/game/1/history")
    assert r.get_json()["points"] == []
    assert "s-maxage=60" in r.headers["Cache-Control"]
