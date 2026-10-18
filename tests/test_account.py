import pytest
import responses

from config import Config
from services import steam, steam_account
from services.http import UpstreamError, get_json, redact

SID = "76561197960287930"


@pytest.mark.parametrize("text,expected", [
    (SID, ("steamid", SID)),
    (f"https://steamcommunity.com/profiles/{SID}/", ("steamid", SID)),
    ("https://steamcommunity.com/id/some_name/games", ("vanity", "some_name")),
    ("  some-name  ", ("vanity", "some-name")),
])
def test_parse_input(text, expected):
    assert steam_account.parse_input(text) == expected


@pytest.mark.parametrize("bad", ["", "a", "not a profile!", "https://steamcommunity.com/profiles/123/"])
def test_parse_input_rejects_garbage(bad):
    with pytest.raises(steam_account.ProfileError):
        steam_account.parse_input(bad)


@responses.activate
def test_resolve_reads_profile_xml():
    responses.get(
        "https://steamcommunity.com/id/some_name",
        body=f"""<?xml version="1.0" encoding="UTF-8"?><profile><steamID64>{SID}</steamID64>
        <steamID><![CDATA[Some Name]]></steamID><avatarMedium><![CDATA[https://avatars.example/a.jpg]]></avatarMedium>
        <privacyState>public</privacyState></profile>""",
    )
    assert steam_account.resolve("some_name") == {
        "steamid": SID, "name": "Some Name", "avatar": "https://avatars.example/a.jpg", "public": True}


@responses.activate
def test_resolve_unknown_or_hostile_xml():
    responses.get("https://steamcommunity.com/id/nobody",
                  body="<response><error><![CDATA[The specified profile could not be found.]]></error></response>")
    with pytest.raises(steam_account.ProfileError):
        steam_account.resolve("nobody")
    responses.get("https://steamcommunity.com/id/evil",
                  body='<?xml version="1.0"?><!DOCTYPE x [<!ENTITY a "aaaa">]><profile><steamID64>&a;</steamID64></profile>')
    with pytest.raises(steam_account.ProfileError):
        steam_account.resolve("evil")


def test_wishlist_puts_good_sales_first(monkeypatch):
    monkeypatch.setattr(steam_account, "wishlist_items", lambda sid: [
        {"appid": 1, "priority": 1, "added": 0}, {"appid": 2, "priority": 2, "added": 0},
        {"appid": 3, "priority": 3, "added": 0}])

    def item(appid, discount, pct):
        return {"appid": appid, "name": f"G{appid}", "small_capsule": None, "header": None, "is_free": False,
                "is_coming_soon": False, "review_count": 1000, "review_pct": pct, "review_label": "Positive",
                "price": {"final": 10.0, "initial": 20.0, "discount": discount, "final_formatted": "$10.00",
                          "initial_formatted": "$20.00"}}

    monkeypatch.setattr(steam, "store_items", lambda ids, cc="US": {1: item(1, 0, 90), 2: item(2, 50, 60), 3: item(3, 50, 95)})
    w = steam_account.wishlist(SID)
    assert [g["appid"] for g in w["games"]] == [3, 2, 1]
    assert w["on_sale"] == 2 and w["count"] == 3


def test_library_needs_a_key(monkeypatch):
    monkeypatch.setattr(Config, "STEAM_API_KEY", "")
    with pytest.raises(steam_account.ProfileError):
        steam_account.owned_games(SID)


@responses.activate
def test_private_library_is_none(monkeypatch):
    monkeypatch.setattr(Config, "STEAM_API_KEY", "SECRETKEY123")
    responses.get("https://api.steampowered.com/IPlayerService/GetOwnedGames/v1/", json={"response": {}})
    assert steam_account.owned_games(SID) is None


def test_api_key_never_leaks_into_errors():
    assert redact("Max retries exceeded with url: /x?key=SECRETKEY123&steamid=1") == \
        "Max retries exceeded with url: /x?key=REDACTED&steamid=1"


@responses.activate
def test_get_json_errors_are_redacted():
    import requests

    responses.get("https://api.example.test/x", body=requests.ConnectionError("failed url: /x?key=SECRETKEY123"))
    with pytest.raises(UpstreamError) as exc:
        get_json("https://api.example.test/x", {"key": "SECRETKEY123"}, retries=0)
    assert "SECRETKEY123" not in str(exc.value)


def test_me_api_statuses(client, monkeypatch):
    r = client.get("/api/me/resolve?q=!!")
    assert r.status_code == 400 and r.headers["Cache-Control"] == "private, no-store"
    assert client.get("/api/me/12345/wishlist").status_code == 404

    monkeypatch.setattr(Config, "STEAM_API_KEY", "")
    r = client.get(f"/api/me/{SID}/library")
    assert r.status_code == 501 and r.get_json()["reason"] == "no_key"

    monkeypatch.setattr(steam_account, "library", lambda sid: None)
    r = client.get(f"/api/me/{SID}/library")
    assert r.status_code == 403 and r.get_json()["reason"] == "private"


def test_me_page_is_a_static_shell(client):
    r = client.get("/me")
    assert r.status_code == 200 and "s-maxage" in r.headers["Cache-Control"]
    assert b"Nothing you enter is stored" in r.data
