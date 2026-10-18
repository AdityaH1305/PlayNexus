import pytest

from services import discover, pool, recommendations, vibes

NAMES = {
    1: "Cozy", 2: "Relaxing", 3: "Farming Sim", 4: "Horror", 5: "Psychological Horror", 6: "Online Co-Op",
    7: "Souls-like", 8: "Difficult", 9: "Singleplayer", 10: "Roguelite", 11: "Story Rich", 12: "Automation",
}


def game(appid, name, tags, *, pct=95, count=50_000, price=19.99, players=("single",), hours=20.0, deck=3):
    return {
        "appid": appid, "name": name, "header": f"https://example.test/{appid}.jpg", "capsule": None,
        "tags": [[t, w] for t, w in tags], "review_pct": pct, "review_count": count, "is_free": price is None,
        "price": price, "deck": deck, "release": 0, "players": list(players),
        "hours": {"p50_all": hours, "p50_recent": 0.0, "p75_recent": 0.0},
    }


GAMES = [
    game(1, "Stardew-ish", [(1, 900), (2, 800), (3, 700), (9, 500)], hours=60),
    game(2, "Cozy Co-op", [(1, 900), (6, 850), (2, 400)], players=("single", "coop"), hours=8),
    game(3, "Scary Thing", [(4, 900), (5, 800), (6, 600)], players=("coop",), price=9.99, hours=6),
    game(4, "Cozy Horror", [(1, 900), (4, 880)], hours=5),
    game(5, "Hard Knight", [(7, 900), (8, 800), (9, 700)], price=14.99, hours=45, pct=97),
    game(6, "Run Again", [(10, 900), (8, 500), (9, 450)], price=24.99, hours=70, deck=2),
    game(7, "Factory Brain", [(12, 900), (9, 300)], price=35.0, hours=150),
    game(8, "Weepy Tale", [(11, 900), (9, 700)], price=None, hours=12, pct=88, count=4000),
]


@pytest.fixture(autouse=True)
def small_pool(monkeypatch):
    pool.reset(pool.build({"built": 1, "games": [dict(g) for g in GAMES]}, NAMES))
    from services import steam

    monkeypatch.setattr(steam, "store_items", lambda ids, cc="US": {})  # no live prices in tests
    yield
    pool.reset()


def test_vibe_scores_are_absolute_and_explained():
    scores = vibes.score_tags([(1, 900), (2, 800), (3, 700)], NAMES)
    assert scores["cozy"]["score"] == 100
    assert scores["cozy"]["why"][:2] == ["Cozy", "Relaxing"]
    assert scores["horror"]["score"] == 0
    # Not normalised to the best vibe: a vaguely-tagged game scores low everywhere.
    weak = vibes.score_tags([(9, 900), (2, 100)], NAMES)
    assert max(v["score"] for v in weak.values()) < vibes.SHOW_AT


def test_negative_tags_pull_a_vibe_down():
    plain = vibes.score_tags([(1, 900)], NAMES)["cozy"]["score"]
    spooky = vibes.score_tags([(1, 900), (4, 880)], NAMES)["cozy"]["score"]
    assert spooky < plain


def test_legacy_vibe_keys_still_work():
    assert vibes.normalize_key("cozy_relaxing") == "cozy"
    assert vibes.normalize_key("pvp_sweaty") == "competitive"
    assert vibes.normalize_key("nope") is None


def names(result):
    return [g["name"] for g in result["results"]]


def test_discover_ranks_games_fitting_every_vibe_first():
    res = discover.discover(["cozy", "coop"])
    assert names(res)[0] == "Cozy Co-op"
    assert res["results"][0]["fits_all"] is True
    assert "Scary Thing" in names(res)  # fits co-op only, so it comes later


def test_discover_filters():
    assert names(discover.discover(["horror"], players="coop")) == ["Scary Thing"]
    assert "Run Again" not in names(discover.discover(["roguelike"], deck=True))
    long_games = names(discover.discover([], length="long"))
    assert set(long_games) == {"Stardew-ish", "Hard Knight", "Run Again", "Factory Brain"}
    assert long_games[0] == "Hard Knight"  # best reviewed first when no vibe is picked
    cheap = names(discover.discover([], max_price=10))
    assert "Factory Brain" not in cheap and "Weepy Tale" in cheap  # free counts as under $10
    assert names(discover.discover([], max_price=0)) == ["Weepy Tale"]


def test_discover_cards_carry_value_and_vibes():
    card = discover.discover(["souls_like"])["results"][0]
    assert card["name"] == "Hard Knight"
    assert card["value"]["score"] is not None
    assert card["vibes"][0]["key"] == "souls_like" and "Souls-like" in card["vibes"][0]["why"]


def test_similar_games_share_distinctive_tags():
    recs = recommendations.get_recommendations(1)
    assert recs and recs[0]["name"] in {"Cozy Co-op", "Cozy Horror"}
    assert "Cozy" in recs[0]["match_reason"]
    assert all(r["appid"] != 1 for r in recs)


def test_discover_page_renders(client):
    html = client.get("/discover?v=cozy&v=coop&v=horror&v=story").get_data(as_text=True)
    assert "Cozy Co-op" in html
    assert html.count('name="v" value="') == len(vibes.VIBES)
    assert html.count(" checked") == 3  # a fourth vibe is ignored
    assert "Pick for me" in html


def test_discover_page_accepts_old_vibe_keys(client):
    html = client.get("/discover?v=cozy_relaxing").get_data(as_text=True)
    assert 'value="cozy" checked' in html
