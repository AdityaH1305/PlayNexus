"""Local prices on list pages: the /api/prices/batch endpoint and the pages
that mark their prices for it.  Steam is mocked with ``responses``."""

import json
import re
from urllib.parse import parse_qs, urlparse

import pytest
import responses

from config import Config
from services import steam, steam_account

APPDETAILS = "https://store.steampowered.com/api/appdetails"
CACHED = "public, max-age=0, s-maxage=3600, stale-while-revalidate=86400"


def overview(currency, final, formatted, *, initial=None, initial_formatted="", discount=0):
    return {"success": True, "data": {"price_overview": {
        "currency": currency, "initial": initial or final, "final": final, "discount_percent": discount,
        "initial_formatted": initial_formatted, "final_formatted": formatted}}}


# What Steam's India store sent on 2026-10-18, give or take a sale.
INDIA = {
    1086940: overview("INR", 149900, "₹ 1,499", initial=299900, initial_formatted="₹ 2,999", discount=50),
    413150: overview("INR", 47900, "₹ 479"),
    730: {"success": True, "data": []},  # free: no price_overview
}


def mock_steam(prices_by_cc, fail_for=()):
    """Answer appdetails price lookups from {cc: {appid: node}}; batches that
    include an app in *fail_for* get a 500."""

    def answer(request):
        qs = parse_qs(urlparse(request.url).query)
        ids = [int(a) for a in qs["appids"][0].split(",")]
        if set(ids) & set(fail_for):
            return 500, {}, "{}"
        known = prices_by_cc.get(qs["cc"][0], {})
        return 200, {}, json.dumps({str(a): known.get(a, {"success": False}) for a in ids})

    responses.add_callback(responses.GET, APPDETAILS, callback=answer)


def steam_calls():
    return [parse_qs(urlparse(c.request.url).query) for c in responses.calls]


@pytest.fixture(autouse=True)
def _no_retry_wait(monkeypatch):
    from services import http

    monkeypatch.setattr(http.time, "sleep", lambda s: None)


@responses.activate
def test_batch_returns_local_prices_with_us_dollar_equivalent(client, fixed_fx):
    mock_steam({"in": INDIA})
    r = client.get("/api/prices/batch?ids=1086940,413150,730&region=IN")
    assert r.status_code == 200
    assert r.headers["Cache-Control"] == CACHED
    body = r.get_json()
    assert body["region"] == "IN"
    bg3 = body["prices"]["1086940"]
    assert bg3["final_formatted"] == "₹ 1,499" and bg3["initial_formatted"] == "₹ 2,999"
    assert bg3["discount"] == 50 and bg3["currency"] == "INR"
    assert bg3["usd"] == round(1499 / fixed_fx["INR"], 2)
    assert body["prices"]["413150"]["discount"] == 0
    assert body["prices"]["730"] is None  # free, or not sold there: keep the US price
    assert body["fx"]["source"] == "test"

    (call,) = steam_calls()  # one batched Steam call
    assert call["cc"] == ["in"] and call["filters"] == ["price_overview"]
    assert sorted(call["appids"][0].split(",")) == ["1086940", "413150", "730"]


@pytest.mark.parametrize("asked,cc,code", [
    ("IN", "in", "IN"),
    ("in", "in", "IN"),
    ("EU", "de", "EU"),  # Steam has no "eu" store; Germany's carries the euro price
    ("eu", "de", "EU"),
    ("de", "de", "EU"),
    ("GB", "gb", "GB"),
    ("TR", "tr", "TR"),
    ("US", "us", "US"),
])
@responses.activate
def test_region_codes_map_to_steam_store_countries(client, fixed_fx, asked, cc, code):
    mock_steam({})
    body = client.get(f"/api/prices/batch?ids=413150&region={asked}").get_json()
    assert body["region"] == code
    assert steam_calls()[0]["cc"] == [cc]


@responses.activate
def test_every_configured_region_is_accepted(client, fixed_fx):
    mock_steam({})
    for region in Config.REGIONS:
        responses.calls.reset()
        r = client.get(f"/api/prices/batch?ids=413150&region={region['code']}")
        assert r.status_code == 200, region
        assert steam_calls()[0]["cc"] == [region["cc"]]


@pytest.mark.parametrize("query", [
    "ids=413150",  # no region
    "ids=413150&region=",
    "ids=413150&region=XX",
    "ids=413150&region=uk",  # Steam calls it gb
    "region=IN",  # no ids
    "ids=&region=IN",
    "ids=,,&region=IN",
    "ids=abc&region=IN",
    "ids=413150,-2&region=IN",
    "ids=0&region=IN",
    "ids=12345678901&region=IN",
    "ids=1;2&region=IN",
    "ids=1.5&region=IN",
    "ids=%C2%B2&region=IN",  # a superscript two is a digit to Python, not to us
])
@responses.activate
def test_bad_requests_are_rejected_and_never_cached(client, query):
    r = client.get(f"/api/prices/batch?{query}")
    assert r.status_code == 400
    assert r.get_json()["error"]
    assert r.headers["Cache-Control"] == "no-store"
    assert not responses.calls


@responses.activate
def test_ids_are_capped_per_request(client, fixed_fx):
    mock_steam({})
    too_many = ",".join(str(1000 + i) for i in range(101))
    r = client.get(f"/api/prices/batch?ids={too_many}&region=IN")
    assert r.status_code == 400 and "100" in r.get_json()["error"]
    assert not responses.calls

    # Repeats count once, and 100 apps go to Steam as two calls of 50.
    ids = [str(1000 + i) for i in range(100)]
    r = client.get(f"/api/prices/batch?ids={','.join(ids + ids[:30])}&region=IN")
    assert r.status_code == 200 and len(r.get_json()["prices"]) == 100
    assert sorted(len(c["appids"][0].split(",")) for c in steam_calls()) == [50, 50]


@responses.activate
def test_steam_failures_are_left_out_and_cached_briefly(client, fixed_fx):
    # 60 apps in two batches: 413150 and 9000-9048 work; 9049-9058 fail.
    ids = [413150] + [9000 + i for i in range(59)]
    mock_steam({"in": INDIA}, fail_for={9058})
    r = client.get(f"/api/prices/batch?ids={','.join(map(str, ids))}&region=IN")
    assert r.status_code == 200
    prices = r.get_json()["prices"]
    assert prices["413150"]["final_formatted"] == "₹ 479"
    assert prices["9048"] is None  # answered: not sold there
    assert "9049" not in prices and "9058" not in prices  # unanswered: the page keeps US prices
    assert r.headers["Cache-Control"] == "public, max-age=0, s-maxage=60, stale-while-revalidate=0"

    # Failures aren't remembered: once Steam answers, they're priced.
    responses.reset()
    mock_steam({"in": {9058: overview("INR", 19900, "₹ 199")}})
    r = client.get(f"/api/prices/batch?ids={','.join(map(str, ids))}&region=IN")
    assert r.get_json()["prices"]["9058"]["final_formatted"] == "₹ 199"
    assert r.headers["Cache-Control"] == CACHED
    assert len(responses.calls) == 1  # only the batch that failed is asked again


def test_price_overview_reports_failed_apps(monkeypatch):
    from services.http import UpstreamError

    def down(url, params=None, **kw):
        raise UpstreamError("down")

    monkeypatch.setattr(steam, "get_json", down)
    failed = set()
    assert steam.price_overview([1, 2], "in", failed=failed) == {1: None, 2: None}
    assert failed == {1, 2}


# ── Wishlist ────────────────────────────────────────────────

def test_wishlist_keeps_value_scores_at_the_us_price(monkeypatch):
    monkeypatch.setattr(steam_account, "wishlist_items", lambda sid: [{"appid": 413150, "priority": 1, "added": 0}])

    def items(ids, cc="US"):
        price = ({"final": 479.0, "initial": 479.0, "discount": 0, "final_formatted": "₹ 479", "initial_formatted": ""}
                 if cc == "IN" else
                 {"final": 14.99, "initial": 14.99, "discount": 0, "final_formatted": "$14.99", "initial_formatted": ""})
        return {413150: {"appid": 413150, "name": "Stardew Valley", "small_capsule": None, "header": None,
                         "is_free": False, "is_coming_soon": False, "review_count": 1000, "review_pct": 98,
                         "review_label": "Overwhelmingly Positive", "price": price}}

    priced_at = []
    monkeypatch.setattr(steam, "store_items", items)
    monkeypatch.setattr(steam_account, "_value_for",
                        lambda appid, price, is_free: priced_at.append(price) or {"score": 90, "label": "x"})

    game = steam_account.wishlist("76561197960287930", "IN")["games"][0]
    assert game["price"]["final_formatted"] == "₹ 479"
    assert priced_at == [14.99]


# ── Pages mark their prices, and stay the same for everyone ─

CHART_ROW = {
    "appid": 367520, "name": "Hollow Knight", "header": None, "capsule": None, "is_free": False,
    "price": {"final": 14.99, "initial": 19.99, "discount": 25, "final_formatted": "$14.99", "initial_formatted": "$19.99"},
    "review_pct": 97, "review_label": "Overwhelmingly Positive", "review_count": 400000,
    "tags": [], "deck": 3, "release_date": 0, "coming_soon": False,
    "rank": 1, "players": 12345, "peak": 23456, "last_week_rank": 4,
}

DEAL = {
    "appid": 1145360, "name": "Hades", "header": None, "capsule": None, "tags": [], "sale_price": 9.99,
    "normal_price": 24.99, "savings": 60, "review_pct": 98, "review_count": 250000, "review_label": "Overwhelmingly Positive",
    "deal_score": 90, "deal_label": "Great deal", "url": "https://www.cheapshark.com/redirect?dealID=x",
    "release_date": 0, "value": {"score": 91, "label": "Excellent value"},
}


@pytest.fixture
def stub_lists(monkeypatch):
    from services import charts, deals, discover

    monkeypatch.setattr(charts, "safe", lambda fn, *a, **k: {"updated": 1792281751, "rows": [dict(CHART_ROW)]})
    monkeypatch.setattr(deals, "current_deals", lambda pages=2: [dict(DEAL)])
    monkeypatch.setattr(discover, "discover", lambda keys, **kw: {"matches": 1, "vibes": [], "results": [{
        "appid": 413150, "name": "Stardew Valley", "header": None, "capsule": None, "review_pct": 98,
        "review_count": 600000, "is_free": False, "hours": 50.0, "deck": 3, "fits_all": False, "vibes": [],
        "price": {"final": 14.99, "initial": 14.99, "discount": 0, "final_formatted": "$14.99", "initial_formatted": ""},
        "value": {"score": 90, "label": "Excellent value"}}]})


def lp_attrs(html):
    return re.findall(r'data-lp="(\d+)" data-lp-usd="([\d.]+)"(?: data-lp-mode="([\w-]+)")?', html)


def test_list_pages_mark_us_prices_for_swapping(client, stub_lists):
    assert lp_attrs(client.get("/charts").get_data(as_text=True)) == [("367520", "14.99", "")]
    assert lp_attrs(client.get("/").get_data(as_text=True)) == [("1145360", "9.99", "sale")]
    assert lp_attrs(client.get("/discover").get_data(as_text=True)) == [("413150", "14.99", "")]
    html = client.get("/deals").get_data(as_text=True)
    assert 'data-lp="1145360" data-lp-usd="9.99" data-lp-mode="sale-row"' in html
    # The US version is what's rendered; the regional note waits for prices.js.
    assert "$14.99" in client.get("/charts").get_data(as_text=True)
    assert 'class="small muted notes region-only"' in html and "data-region-name" in html


@pytest.mark.parametrize("path", ["/", "/charts", "/deals", "/discover", "/compare", "/me"])
def test_cached_pages_are_the_same_for_every_visitor(client, stub_lists, path):
    plain = client.get(path)
    indian = client.get(path, headers={"X-Vercel-IP-Country": "IN", "Accept-Language": "hi-IN,en-IN;q=0.9"})
    assert "s-maxage" in plain.headers["Cache-Control"]
    assert plain.get_data() == indian.get_data()


def test_every_page_gets_the_regions_and_the_switch(client, stub_lists):
    html = client.get("/charts").get_data(as_text=True)
    m = re.search(r'<script type="application/json" id="pn-regions">(.*?)</script>', html, re.S)
    assert [r["code"] for r in json.loads(m.group(1))] == [r["code"] for r in Config.REGIONS]
    switch = re.search(r'<select class="region-switch__select" aria-label="Price region">(.*?)</select>', html, re.S)
    assert len(re.findall(r"<option ", switch.group(1))) == len(Config.REGIONS)
    assert '"pn/prices"' in html
