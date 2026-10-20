"""The game page's "Before you buy" notes and its recent-reviews warning."""

import datetime as dt
import html
import re
from concurrent.futures import Future

import pytest

from conftest import make_game
from services import game as game_service
from services import pool, pricing, reviews, steam
from services.http import UpstreamError

STORE = "https://store.steampowered.com/app/1"


def histogram(recent, all_time):
    """Steam's review histogram with (up, down) totals for the last 30 days and for all time."""
    return {
        "recent": [{"date": 2, "recommendations_up": recent[0], "recommendations_down": recent[1]}],
        "rollups": [{"date": 1, "recommendations_up": all_time[0], "recommendations_down": all_time[1]}],
    }


# ── The recent-reviews warning ──────────────────────────────

def test_much_worse_lately():
    t = reviews.trend(histogram((280, 220), (75_000, 25_000)), 75.4)
    assert t == {"direction": "worse", "recent_pct": 56, "recent_count": 500, "overall_pct": 75}


def test_much_better_lately():
    t = reviews.trend(histogram((329, 77), (24_000, 30_000)), 47.0)
    assert t == {"direction": "better", "recent_pct": 81, "recent_count": 406, "overall_pct": 47}


@pytest.mark.parametrize("hist, overall", [
    (histogram((20, 20), (75_000, 25_000)), 75),     # too few recent reviews to judge
    (histogram((280, 220), (1_100, 700)), 75),       # a new game: the last 30 days are most of its reviews
    (histogram((340, 160), (75_000, 25_000)), 75),   # 68% against 75%: not a big enough change
    (histogram((340, 160), (52_000, 48_000)), 80),   # up 16 on the histogram, but below the page's score
    (None, 75),
    ({}, 75),
])
def test_no_warning(hist, overall):
    assert reviews.trend(hist, overall) is None


def done(value):
    f = Future()
    f.set_result(value)
    return f


SUMMARY = {"total_positive": 7540, "total_negative": 2460, "review_score_desc": "Mostly Positive"}
WORSE = histogram((280, 220), (75_000, 25_000))


def test_review_stats_carry_the_trend():
    stats = reviews.assemble_review_stats((done(SUMMARY), done([]), done([]), done(WORSE)))
    assert stats["trend"]["direction"] == "worse" and stats["trend"]["overall_pct"] == 75


def test_no_trend_without_steams_summary():
    stats = reviews.assemble_review_stats((done(None), done([]), done([]), done(WORSE)))
    assert stats["available"] is False and stats["trend"] is None


def test_the_histogram_is_fetched_with_the_reviews(monkeypatch):
    monkeypatch.setattr(steam, "review_summary", lambda appid: SUMMARY)
    monkeypatch.setattr(steam, "reviews_page", lambda appid, filt="recent": [])
    monkeypatch.setattr(steam, "review_histogram", lambda appid: WORSE)
    assert reviews.get_review_stats(1)["trend"]["direction"] == "worse"

    def busy(appid):
        raise UpstreamError("429")

    monkeypatch.setattr(steam, "review_histogram", busy)
    stats = reviews.get_review_stats(1)
    assert stats["available"] and stats["trend"] is None  # no warning, nothing else lost


# ── Notes from Steam's store data ───────────────────────────

TODAY = dt.date(2026, 10, 21)
EARLY_ACCESS = {"genres": [{"id": "70", "description": "Early Access"}]}


def notes(details, *, trend=None, is_free=False, release="Mar 2, 2026", coming_soon=False):
    return game_service.buyer_notes(details, trend=trend, is_free=is_free, release=release,
                                    coming_soon=coming_soon, store_url=STORE, today=TODAY)


def test_most_games_have_nothing_to_flag():
    assert notes({"controller_support": "full", "categories": [{"id": 2, "description": "Single-player"}],
                  "genres": [{"id": "1", "description": "Action"}]}) == []


def test_account_and_drm_notes_use_steams_words():
    n = notes({"ext_user_account_notice": "EA Account (Supports Linking to Steam Account)",
               "drm_notice": "EA on-line activation and Origin client software installation and background use required"})
    assert [(x["kind"], x["title"], x["detail"]) for x in n] == [
        ("account", "Needs a third-party account", "EA Account (Supports Linking to Steam Account)"),
        ("drm", "Third-party DRM",
         "EA on-line activation and Origin client software installation and background use required"),
    ]


def test_notices_lose_their_markup_and_stay_short():
    [n] = notes({"drm_notice": "Denuvo<br>Anti-tamper &amp; <b>more</b>"})
    assert n["detail"] == "Denuvo Anti-tamper & more"
    [n] = notes({"drm_notice": "word " * 100})
    assert len(n["detail"]) <= 200 and n["detail"].endswith("…")


def test_in_app_purchase_wording_depends_on_the_price():
    iap = {"categories": [{"id": 35, "description": "In-App Purchases"}]}
    assert notes(iap)[0]["title"] == "In-app purchases on top of the price"
    assert notes(iap, is_free=True)[0]["title"] == "Free to play, with in-app purchases"


def test_a_demo_links_to_its_store_page():
    [n] = notes({"demos": [{"appid": 2231770, "description": ""}]})
    assert (n["kind"], n["tone"], n["href"], n["link"]) == (
        "demo", "pos", "https://store.steampowered.com/app/2231770", "Get the demo")


@pytest.mark.parametrize("release, coming_soon, title, detail", [
    ("Oct 23, 2023", False, "In Early Access for almost 3 years", "Since Oct 23, 2023. It isn't finished"),
    ("Dec 6, 2024", False, "In Early Access for almost 2 years", "Since Dec 6, 2024. It isn't finished"),
    ("Jun 10, 2025", False, "In Early Access for over a year", "Since Jun 10, 2025. It isn't finished"),
    ("Dec 6, 2025", False, "In Early Access since Dec 6, 2025", "It isn't finished"),
    ("Coming soon", True, "Launches in Early Access", "It isn't finished"),
    ("", False, "In Early Access", "It isn't finished"),
])
def test_early_access_says_for_how_long(release, coming_soon, title, detail):
    [n] = notes(EARLY_ACCESS, release=release, coming_soon=coming_soon)
    assert n["title"] == title and n["detail"].startswith(detail)


def test_the_review_warning_comes_first():
    trend = {"direction": "worse", "recent_pct": 56, "recent_count": 6228, "overall_pct": 75}
    n = notes({"categories": [{"id": 35}], "demos": [{"appid": 5}], **EARLY_ACCESS}, trend=trend)
    assert [x["kind"] for x in n] == ["trend", "early_access", "iap", "demo"]
    assert (n[0]["tone"], n[0]["icon"], n[0]["title"]) == ("neg", "down", "Recent reviews are much more negative")
    assert n[0]["detail"] == "56% positive in the last 30 days (6,228 reviews), against 75% overall."
    assert (n[0]["href"], n[0]["link"]) == (f"{STORE}#app_reviews_hash", "Read recent reviews on Steam")

    better = notes({}, trend={**trend, "direction": "better"})[0]
    assert (better["tone"], better["icon"], better["title"]) == ("pos", "up", "Recent reviews are much more positive")


@pytest.fixture
def quiet_steam(monkeypatch):
    """Everything else load_game asks Steam for, with nothing much in it."""
    monkeypatch.setattr(steam, "store_items", lambda ids, cc="US": {})
    monkeypatch.setattr(pricing, "submit_regional", lambda appid: {})
    monkeypatch.setattr(pricing, "assemble_regional", lambda futures, is_free=False: {
        "is_free": False, "available": True, "regions": [], "us_usd": 39.99, "reference_usd": 39.99,
        "cheapest": None, "priciest": None, "fx": {"live": False, "source": "test", "updated": 0}})
    monkeypatch.setattr(reviews, "submit_review_calls", lambda appid: None)
    monkeypatch.setattr(reviews, "assemble_review_stats", lambda futures: {
        **make_game()["reviews"],
        "trend": {"direction": "better", "recent_pct": 81, "recent_count": 406, "overall_pct": 47}})
    monkeypatch.setattr(pool, "get", lambda appid: None)


def test_load_game_adds_the_notes(quiet_steam, monkeypatch):
    monkeypatch.setattr(steam, "appdetails", lambda appid, cc="us": {
        "name": "PAYDAY 3", "is_free": False, "controller_support": "full",
        "ext_user_account_notice": "Starbreeze Nebula (Supports Linking to Steam Account)",
        "categories": [{"id": 35, "description": "In-App Purchases"}]})
    g = game_service.load_game(1272080)
    assert [n["kind"] for n in g["notes"]] == ["trend", "account", "iap"]
    assert g["notes"][0]["href"] == "https://store.steampowered.com/app/1272080#app_reviews_hash"
    assert g["meta"]["controller"] == "Full support"


# ── The page ────────────────────────────────────────────────

NOTES = [
    {"kind": "trend", "tone": "neg", "icon": "down", "title": "Recent reviews are much more negative",
     "detail": "56% positive in the last 30 days (6,228 reviews), against 75% overall.",
     "href": "https://store.steampowered.com/app/1086940#app_reviews_hash", "link": "Read recent reviews on Steam"},
    {"kind": "demo", "tone": "pos", "icon": "gamepad", "title": "Free demo on Steam",
     "detail": "Try it before you buy.", "href": "https://store.steampowered.com/app/2231770", "link": "Get the demo"},
]
TREND = {"direction": "worse", "recent_pct": 56, "recent_count": 6228, "overall_pct": 75}


def text(fragment):
    return " ".join(html.unescape(re.sub(r"<[^>]+>", " ", fragment)).split())


def facts(page):
    return re.search(r'<ul class="facts">(.*?)</ul>', page, re.S).group(1)


@pytest.fixture
def render(client, monkeypatch):
    """The game page as rendered from *game*, a bundle like load_game's."""
    def get(game):
        monkeypatch.setattr(game_service, "load_game", lambda appid: game)
        return client.get(f"/game/{game['appid']}").get_data(as_text=True)

    return get


def test_the_panel_lists_the_notes_with_their_links(render):
    page = render(make_game(notes=NOTES, reviews={**make_game()["reviews"], "trend": TREND}))
    panel = re.search(r'<section class="panel" id="before-you-buy".*?</section>', page, re.S).group(0)
    assert text(panel) == (
        "Before you buy Recent reviews are much more negative 56% positive in the last 30 days (6,228 reviews), "
        "against 75% overall. Read recent reviews on Steam Free demo on Steam Try it before you buy. Get the demo")
    assert '<a href="https://store.steampowered.com/app/2231770" target="_blank" rel="noopener">' in panel
    assert "buy-note--neg" in panel and "#i-down" in panel

    header = facts(page)
    assert "Last 30 days 56%" in text(header) and "delta--down" in header
    assert 'title="56% positive of 6,228 reviews"' in header


def test_nothing_to_say_means_no_panel_and_no_header_warning(render):
    page = render(make_game())
    assert 'id="before-you-buy"' not in page and "Last 30 days" not in page


def test_about_lists_controller_support(render):
    about = re.search(r'<dl class="about__list">(.*?)</dl>', render(make_game()), re.S).group(1)
    assert "Controller Full support" in text(about)


def test_review_percentages_round_to_the_nearest_point(render):
    header = facts(render(make_game(reviews={**make_game()["reviews"], "positivity": 0.759})))
    assert "76%" in text(header) and 'title="76% positive' in header
