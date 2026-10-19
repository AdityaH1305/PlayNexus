import html
import json
import re
from urllib.parse import parse_qs, urlparse

import pytest
import responses

from config import Config
from conftest import make_game
from services import pool, pricing, reviews, steam

APPDETAILS = "https://store.steampowered.com/api/appdetails"

# What Steam returned for ELDEN RING on 2026-10-18 (subset of regions).
ELDEN = {
    "us": ("USD", 5999, "$59.99"),
    "de": ("EUR", 5999, "59,99€"),
    "in": ("INR", 359900, "₹ 3,599"),
    "tr": ("USD", 3999, "$39.99 USD"),
    "jp": ("JPY", 902000, "¥ 9,020"),
    "ae": ("AED", 24900, "249.00 AED"),
}


def price_lookups(prices, down=()):
    """A ``responses`` callback for Steam's appdetails price lookups, answering
    from {cc: (currency, cents, formatted)}.  Stores in *down* answer HTTP 500;
    the rest don't sell the app."""

    def answer(request):
        qs = parse_qs(urlparse(request.url).query)
        cc = qs["cc"][0]
        if cc in down:
            return 500, {}, "{}"
        node = {"success": False}
        if cc in prices:
            cur, cents, fmt = prices[cc]
            node = {"success": True, "data": {"price_overview": {
                "currency": cur, "initial": cents, "final": cents, "discount_percent": 0,
                "final_formatted": fmt, "initial_formatted": ""}}}
        return 200, {}, json.dumps({appid: node for appid in qs["appids"][0].split(",")})

    return answer


@pytest.fixture(autouse=True)
def _no_retry_wait(monkeypatch):
    from services import http

    monkeypatch.setattr(http.time, "sleep", lambda s: None)


@pytest.fixture
def fake_prices(fixed_fx):
    """Steam answers with ELDEN RING's prices, except the stores in the set
    this yields (South Korea's, to start with), which answer HTTP 500."""
    down = {"kr"}
    with responses.RequestsMock() as rsps:
        rsps.add_callback(responses.GET, APPDETAILS, callback=price_lookups(ELDEN, down))
        yield down


def by_code(result):
    return {r["code"]: r for r in result["regions"]}


def test_regions_cover_config_and_euro_area_uses_german_store():
    eu = Config.region("eu")
    assert eu["cc"] == "de" and eu["code"] == "EU"
    assert len({r["cc"] for r in Config.REGIONS}) == len(Config.REGIONS)


def test_regional_prices_convert_with_live_rates(fake_prices):
    res = pricing.regional_prices(1245620)
    rows = by_code(res)
    assert res["us_usd"] == 59.99
    assert rows["EU"]["final_formatted"] == "59,99€"
    assert rows["EU"]["usd"] == round(59.99 / 0.8897, 2)
    assert rows["IN"]["usd"] == round(3599 / 95.08, 2)
    assert rows["JP"]["final"] == 9020.0
    assert rows["IN"]["vs_us_pct"] == round((rows["IN"]["usd"] - 59.99) / 59.99 * 100, 1)


def test_cheapest_priciest_and_usd_tier(fake_prices):
    res = pricing.regional_prices(1245620)
    rows = by_code(res)
    assert res["cheapest"] == "IN" and rows["IN"]["is_cheapest"]
    assert res["priciest"] == "AE" and rows["AE"]["is_priciest"]
    assert rows["TR"]["usd_priced"] is True and rows["US"]["usd_priced"] is False


def test_unsold_and_failed_regions_are_told_apart(fake_prices):
    rows = by_code(pricing.regional_prices(1245620))
    assert rows["GB"]["available"] is False and rows["GB"]["error"] is False  # answered: not sold there
    assert rows["KR"]["available"] is False and rows["KR"]["error"] is True  # HTTP 500, even on the retry


def test_price_api_is_cached_briefly_when_a_region_failed(client, fake_prices, monkeypatch):
    monkeypatch.setattr(steam, "appdetails", lambda appid, cc="us": {"name": "ELDEN RING", "is_free": False})
    r = client.get("/api/prices/1245620")
    assert by_code(r.get_json())["KR"]["error"] is True
    assert r.headers["Cache-Control"] == "public, max-age=0, s-maxage=60, stale-while-revalidate=0"

    fake_prices.discard("kr")
    r = client.get("/api/prices/1245620")
    assert by_code(r.get_json())["KR"]["error"] is False
    assert r.headers["Cache-Control"] == "public, max-age=0, s-maxage=10800, stale-while-revalidate=86400"


def test_free_games_skip_price_lookups(monkeypatch, fixed_fx):
    monkeypatch.setattr(steam, "price_overview", lambda *a, **k: pytest.fail("should not be called"))
    res = pricing.regional_prices(730, is_free=True)
    assert res["is_free"] and res["regions"] == [] and res["reference_usd"] == 0.0


@responses.activate
def test_reference_price_falls_back_to_median_without_us(fixed_fx):
    prices = {"in": ("INR", 95080, "₹ 950.80"), "jp": ("JPY", 316000, "¥ 3,160")}
    responses.add_callback(responses.GET, APPDETAILS, callback=price_lookups(prices))
    res = pricing.regional_prices(1)
    assert res["us_usd"] is None
    assert res["reference_usd"] == pytest.approx(15.0, abs=0.01)  # median of $10 and $20


# ── The game page's price table ─────────────────────────────

ITEM = {
    "appid": 1245620, "name": "ELDEN RING", "type": 0, "header": "h", "hero": None, "tags": [], "deck": 3,
    "review_count": 700000, "review_pct": 93, "review_label": "Very Positive",
}


@pytest.fixture
def rest_of_game_page(monkeypatch):
    """Everything on the game page except prices, as if Steam had answered."""
    monkeypatch.setattr(steam, "appdetails", lambda appid, cc="us": {"name": "ELDEN RING", "is_free": False})
    monkeypatch.setattr(steam, "store_items", lambda ids, cc="US": {1245620: dict(ITEM)})
    monkeypatch.setattr(reviews, "submit_review_calls", lambda appid: None)
    monkeypatch.setattr(reviews, "assemble_review_stats", lambda futures: dict(make_game()["reviews"]))
    monkeypatch.setattr(pool, "get", lambda appid: None)


def price_row(page, code):
    """One region's row of the price table, as plain text."""
    row = re.search(rf'<tr data-region="{code}">(.*?)</tr>', page, re.S).group(1)
    return " ".join(html.unescape(re.sub(r"<[^>]+>", " ", row)).split())


def test_game_page_says_which_region_failed_and_is_cached_briefly(client, fake_prices, rest_of_game_page):
    r = client.get("/game/1245620")
    page = r.get_data(as_text=True)
    assert "Couldn't load" in price_row(page, "KR")
    assert "Not sold" in price_row(page, "GB")
    assert r.headers["Cache-Control"] == "public, max-age=0, s-maxage=60, stale-while-revalidate=0"

    # Failures aren't remembered: once Korea answers, the page is whole and cached as usual.
    fake_prices.discard("kr")
    r = client.get("/game/1245620")
    assert "Couldn't load" not in html.unescape(r.get_data(as_text=True))
    assert r.headers["Cache-Control"] == "public, max-age=0, s-maxage=1800, stale-while-revalidate=86400"
