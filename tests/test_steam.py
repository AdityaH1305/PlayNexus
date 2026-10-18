import json
from urllib.parse import parse_qs, urlparse

import responses

from services import steam


@responses.activate
def test_appdetails_returns_none_for_unknown_apps():
    responses.get(
        "https://store.steampowered.com/api/appdetails",
        json={"999": {"success": False}},
    )
    assert steam.appdetails(999) is None


@responses.activate
def test_price_overview_batches_and_handles_free_or_unsold_apps():
    responses.get(
        "https://store.steampowered.com/api/appdetails",
        json={
            "1245620": {"success": True, "data": {"price_overview": {
                "currency": "JPY", "initial": 902000, "final": 902000, "discount_percent": 0,
                "final_formatted": "¥ 9,020"}}},
            "730": {"success": True, "data": []},
        },
    )
    got = steam.price_overview([1245620, 730], "jp")
    assert got[1245620]["final_formatted"] == "¥ 9,020"
    assert got[730] is None
    assert len(responses.calls) == 1
    qs = parse_qs(urlparse(responses.calls[0].request.url).query)
    assert qs["appids"] == ["1245620,730"] and qs["filters"] == ["price_overview"]

    steam.price_overview([730], "jp")  # cached, including the "none"
    assert len(responses.calls) == 1


@responses.activate
def test_store_items_parses_art_tags_price_and_deck():
    responses.get(
        "https://api.steampowered.com/IStoreBrowseService/GetItems/v1/",
        json={"response": {"store_items": [{
            "success": 1, "appid": 367520, "name": "Hollow Knight", "type": 0,
            "assets": {"asset_url_format": "steam/apps/367520/${FILENAME}?t=1",
                       "header": "abc/header.jpg", "library_hero": "def/library_hero.jpg"},
            "reviews": {"summary_filtered": {"review_count": 400000, "percent_positive": 97,
                                             "review_score_label": "Overwhelmingly Positive"}},
            "tags": [{"tagid": 1695, "weight": 900}, {"tagid": 1628, "weight": 800}],
            "platforms": {"windows": True, "steamos_linux": True, "steam_deck_compat_category": 3},
            "best_purchase_option": {"final_price_in_cents": "1499", "formatted_final_price": "$14.99"},
            "basic_info": {"developers": [{"name": "Team Cherry"}], "short_description": "Bugs."},
        }]}},
    )
    item = steam.store_items([367520])[367520]
    assert item["header"] == (
        "https://shared.akamai.steamstatic.com/store_item_assets/steam/apps/367520/abc/header.jpg?t=1"
    )
    assert item["deck"] == 3 and item["platforms"]["linux"] is True
    assert item["price"] == {"final": 14.99, "initial": 14.99, "discount": 0,
                             "final_formatted": "$14.99", "initial_formatted": ""}
    assert item["tags"][0] == (1695, 900)
    assert item["developers"] == ["Team Cherry"]

    sent = json.loads(parse_qs(urlparse(responses.calls[0].request.url).query)["input_json"][0])
    assert sent["ids"] == [{"appid": 367520}] and sent["context"]["country_code"] == "US"
