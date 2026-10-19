"""The regional price index (/regions) when Steam doesn't answer for a store.
Steam is mocked with ``responses``."""

import html
import json
import time
from urllib.parse import parse_qs, urlparse

import pytest
import responses

from services import charts, http, price_index

APPDETAILS = "https://store.steampowered.com/api/appdetails"
DEGRADED = "public, max-age=0, s-maxage=60, stale-while-revalidate=0"

GAMES = [{"appid": 1000 + i, "name": f"Game {i}", "is_free": False, "price": {"final": 20.0}} for i in range(12)]

# Every game costs $20 in the US; India charges half that, Korea 80 %, the euro area the same.
STORES = {"us": ("USD", 2000), "in": ("INR", 95080), "kr": ("KRW", 2135360), "de": ("EUR", 1779)}


def mock_steam(down):
    """Answer Steam's price lookups from STORES; stores in *down* answer HTTP 500,
    and the rest don't sell the games."""

    def answer(request):
        qs = parse_qs(urlparse(request.url).query)
        cc = qs["cc"][0]
        if cc in down:
            return 500, {}, "{}"
        node = {"success": False}
        if cc in STORES:
            currency, cents = STORES[cc]
            node = {"success": True, "data": {"price_overview": {
                "currency": currency, "initial": cents, "final": cents, "discount_percent": 0}}}
        return 200, {}, json.dumps({appid: node for appid in qs["appids"][0].split(",")})

    responses.add_callback(responses.GET, APPDETAILS, callback=answer)


@pytest.fixture(autouse=True)
def _no_retry_wait(monkeypatch):
    monkeypatch.setattr(http.time, "sleep", lambda s: None)


@pytest.fixture
def basket(monkeypatch, fixed_fx):
    """This week's top sellers are the twelve games above."""
    monkeypatch.setattr(charts, "top_sellers", lambda: {"rows": GAMES})
    monkeypatch.setattr(charts, "playing_now", lambda: {"rows": []})


@responses.activate
def test_a_store_that_fails_is_left_out_and_named(basket):
    mock_steam(down={"kr"})
    with pytest.raises(price_index.PartialIndex) as failure:
        price_index.compute()
    index = failure.value.index
    assert [r["code"] for r in index["rows"]] == ["IN", "EU"]
    assert index["missing"] == ["South Korea"]
    assert all(g["code"] != "KR" for g in index["gaps"])


@responses.activate
def test_without_us_prices_there_is_no_index(client, basket):
    mock_steam(down={"us"})
    r = client.get("/regions")
    assert "The index isn't available right now" in html.unescape(r.get_data(as_text=True))
    assert r.headers["Cache-Control"] == DEGRADED


@responses.activate
def test_regions_page_names_failed_stores_and_asks_again_soon(client, basket, monkeypatch):
    down = {"kr"}
    mock_steam(down)
    r = client.get("/regions")
    page = html.unescape(r.get_data(as_text=True))
    assert "Couldn't load prices for South Korea." in page
    assert page.count('class="index__row"') == 2
    assert r.headers["Cache-Control"] == DEGRADED

    # Six minutes on (a partial index is kept for five), Korea answers again.
    down.clear()
    later = time.monotonic() + 6 * 60
    monkeypatch.setattr(http.time, "monotonic", lambda: later)
    r = client.get("/regions")
    page = html.unescape(r.get_data(as_text=True))
    assert "Couldn't load" not in page
    assert page.count('class="index__row"') == 3
    assert r.headers["Cache-Control"] == "public, max-age=0, s-maxage=21600, stale-while-revalidate=259200"
