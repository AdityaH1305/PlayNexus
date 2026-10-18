import responses

from services import cheapshark

BASE = "https://www.cheapshark.com/api/1.0"


@responses.activate
def test_game_deals_sorted_with_all_time_low():
    responses.get(f"{BASE}/stores", json=[
        {"storeID": "1", "storeName": "Steam", "isActive": 1, "images": {"icon": "/img/stores/icons/0.png"}},
        {"storeID": "7", "storeName": "GOG", "isActive": 1, "images": {"icon": "/img/stores/icons/6.png"}},
        {"storeID": "99", "storeName": "Gone", "isActive": 0, "images": {}},
    ])
    responses.get(f"{BASE}/games", match=[responses.matchers.query_param_matcher({"steamAppID": "1245620"})],
                  json=[{"gameID": "236717", "steamAppID": "1245620"}])
    responses.get(f"{BASE}/games", match=[responses.matchers.query_param_matcher({"id": "236717"})], json={
        "cheapestPriceEver": {"price": "29.95", "date": 1751353695},
        "deals": [
            {"storeID": "1", "dealID": "a", "price": "59.99", "retailPrice": "59.99", "savings": "0"},
            {"storeID": "7", "dealID": "b", "price": "39.99", "retailPrice": "59.99", "savings": "33.3"},
            {"storeID": "99", "dealID": "c", "price": "1.00", "retailPrice": "59.99", "savings": "98"},
        ],
    })
    got = cheapshark.game_deals(1245620)
    assert got["cheapest_ever"] == {"price": 29.95, "date": 1751353695}
    assert [d["store"] for d in got["deals"]] == ["GOG", "Steam"]  # inactive store dropped, cheapest first
    assert got["deals"][0]["url"] == "https://www.cheapshark.com/redirect?dealID=b"
    assert got["deals"][0]["savings"] == 33


@responses.activate
def test_unknown_games_return_none():
    responses.get(f"{BASE}/stores", json=[])
    responses.get(f"{BASE}/games", json=[])
    assert cheapshark.game_deals(1) is None
