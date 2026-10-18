import pytest

from services import deals
from services.http import UpstreamError


def row(appid, price, savings, pct, count=5000):
    score = deals.deal_score(savings, pct, count)
    return {
        "appid": appid, "name": f"Game {appid}", "header": None, "capsule": None, "tags": [],
        "sale_price": price, "normal_price": round(price / (1 - savings / 100), 2), "savings": savings,
        "review_pct": pct, "review_count": count, "review_label": "Very Positive", "deal_score": score,
        "deal_label": deals.deal_label(score), "url": "https://www.cheapshark.com/redirect?dealID=x",
        "release_date": 0,
    }


def test_deal_score_rewards_discount_reviews_and_volume():
    assert deals.deal_score(90, 100, 100_000) == 100
    assert deals.deal_score(0, 50, 10) == 3  # only the confidence floor
    assert deals.deal_score(75, 95, 50_000) > deals.deal_score(75, 70, 50_000)
    assert deals.deal_score(75, 95, 50_000) > deals.deal_score(75, 95, 50)
    assert deals.deal_score(80, None, 0) == deals.deal_score(80, 0, 0)


def test_labels():
    assert [deals.deal_label(s) for s in (80, 65, 50, 20)] == ["Great deal", "Good deal", "Fair deal", "So-so deal"]


def test_filters_and_sorts():
    rows = [row(1, 3.99, 90, 96), row(2, 19.99, 50, 80), row(3, 1.99, 80, 60), row(4, 39.99, 25, 98)]
    assert [r["appid"] for r in deals.filter_deals(rows, max_price=5)] == [1, 3]
    assert [r["appid"] for r in deals.filter_deals(rows, min_rating=90)] == [1, 4]
    assert [r["appid"] for r in deals.filter_deals(rows, min_discount=75, sort="price")] == [3, 1]
    assert deals.filter_deals(rows, sort="reviews")[0]["appid"] == 4
    assert deals.filter_deals(rows, sort="nonsense")[0]["appid"] == 1  # falls back to best deal


def test_current_deals_drops_non_games_and_prefers_steam_reviews(monkeypatch):
    from services import cheapshark, steam

    feed = [
        {"appid": 10, "name": "Real Game", "sale_price": 4.99, "normal_price": 19.99, "savings": 75,
         "rating_pct": 70, "rating_count": 100, "rating_text": "Mostly Positive", "metacritic": None,
         "release_date": 0, "deal_rating": 9, "thumb": "t", "url": "u"},
        {"appid": 11, "name": "Some DLC", "sale_price": 0.99, "normal_price": 4.99, "savings": 80,
         "rating_pct": 99, "rating_count": 5000, "rating_text": "x", "metacritic": None,
         "release_date": 0, "deal_rating": 10, "thumb": "t", "url": "u"},
    ]
    monkeypatch.setattr(cheapshark, "steam_deals", lambda page=0, **k: feed if page == 0 else [])

    def items(ids, cc="US"):
        base = {"header": "h", "small_capsule": "c", "tags": [], "review_label": "Very Positive"}
        return {10: {**base, "type": 0, "name": "Real Game", "review_pct": 92, "review_count": 20000},
                11: {**base, "type": 4, "name": "Some DLC", "review_pct": 99, "review_count": 5000}}

    monkeypatch.setattr(steam, "store_items", items)
    out = deals.current_deals()
    assert [r["appid"] for r in out] == [10]
    assert out[0]["review_pct"] == 92 and out[0]["review_count"] == 20000


@pytest.fixture
def stub_deals(monkeypatch):
    rows = [row(1, 3.99, 90, 96), row(2, 19.99, 50, 80)]
    monkeypatch.setattr(deals, "current_deals", lambda pages=2: rows)
    return rows


def test_deals_page_filters_from_query(client, stub_deals):
    html = client.get("/deals?max=5").get_data(as_text=True)
    assert "Game 1" in html and "Game 2" not in html
    assert "1 of 2 games on sale" in html


def test_deals_page_handles_outage(client, monkeypatch):
    def down(pages=2):
        raise UpstreamError("down")

    monkeypatch.setattr(deals, "current_deals", down)
    r = client.get("/deals")
    assert r.status_code == 200 and b"Deals aren" in r.data


def test_regions_page_renders_index(client, monkeypatch):
    from services import price_index

    monkeypatch.setattr(price_index, "compute", lambda: {
        "rows": [{"cc": "in", "code": "IN", "name": "India", "currency": "INR", "count": 50, "median_pct": 54,
                  "q1_x": 30, "q3_x": 45, "median_x": 36, "dots": [20, 30, 40]}],
        "basket": 60, "gaps": [{"appid": 1, "name": "Cheap Game", "code": "IN", "region": "India", "pct": 25}],
        "us_x": 66.67, "updated": 0, "fx": {"source": "test"},
    })
    html = client.get("/regions").get_data(as_text=True)
    assert "54%" in html and "Cheap Game" in html and "We priced 60" in html
