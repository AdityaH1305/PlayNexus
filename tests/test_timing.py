"""Buy now or wait? (services.timing): the verdict, one game's inputs, the API and the panel."""

import datetime as dt

import pytest

from config import Config
from conftest import make_game
from services import game as game_service
from services import itad, steam, steam_sales, timing
from services.http import UpstreamError

D = dt.date
TODAY = D(2026, 10, 21)
RELEASE = D(2024, 3, 1)


def sale(name, start, end):
    return {"name": name, "start": D.fromisoformat(start), "end": D.fromisoformat(end)}


SALES = (
    sale("Summer Sale", "2025-06-26", "2025-07-10"),
    sale("Autumn Sale", "2025-09-29", "2025-10-06"),
    sale("Winter Sale", "2025-12-18", "2026-01-05"),
    sale("Spring Sale", "2026-03-19", "2026-03-26"),
    sale("Summer Sale", "2026-06-25", "2026-07-09"),
    sale("Autumn Sale", "2026-10-01", "2026-10-08"),
    sale("Winter Sale", "2026-12-17", "2027-01-04"),
    sale("Spring Sale", "2027-03-18", "2027-03-25"),
)
PAST = SALES[:6]  # the ones over by TODAY


@pytest.fixture(autouse=True)
def calendar(monkeypatch):
    monkeypatch.setattr(steam_sales, "sales", lambda: SALES)


def ev(day, price, regular=5999):
    return {"day": D.fromisoformat(day), "price": price, "regular": regular}


def joiner(price=2399, regular=5999, sales=PAST):
    """A game at full price except in each of *sales*, at *price*."""
    events = [ev("2024-03-01", regular, regular)]
    for s in sales:
        events += [ev(s["start"].isoformat(), price, regular), ev(s["end"].isoformat(), regular, regular)]
    return events


def full(price=5999):
    return timing.Now(price, price)


def texts(v):
    return [f["text"] for f in v["facts"]]


# ── The verdict ─────────────────────────────────────────────

def test_a_regular_big_sale_joiner_should_wait_for_the_next_one():
    v = timing.verdict(joiner(), full(), release=RELEASE, today=TODAY)
    assert (v["verdict"], v["title"]) == ("wait", "Wait for the Winter Sale")
    assert v["summary"] == "It was −60% in 4 of its last 4 big Steam sales, so expect about $23.99."
    assert v["expected"] == {"price": 23.99, "cut": 60, "by": "2027-01-04"}
    assert v["next_sale"] == {"name": "Winter Sale", "start": "2026-12-17", "end": "2027-01-04"}
    assert texts(v) == [
        "Now $59.99, not on sale.",
        "Lowest ever on Steam: $23.99 (−60%), in the Autumn Sale 2026.",
        "On sale in 4 of its last 4 big Steam sales, most recently −60%.",
        "Winter Sale: 17 Dec – 4 Jan 2027.",
    ]
    assert v["since"] == "2024-03-01"


def test_a_new_base_price_scales_the_expected_sale_price():
    v = timing.verdict(joiner() + [ev("2026-10-12", 6999, 6999)], full(6999), release=RELEASE, today=TODAY)
    assert v["verdict"] == "wait" and v["expected"]["price"] == 27.99  # Steam rounds down to the cent


def test_a_price_rise_can_keep_a_game_out_of_the_next_big_sale():
    # No discounts for 30 days after 10 Dec, so the game misses the whole Winter Sale.
    v = timing.verdict(joiner() + [ev("2026-12-10", 6999, 6999)], full(6999), release=RELEASE, today=D(2026, 12, 12))
    assert v["next_sale"]["name"] == "Spring Sale"
    assert v["verdict"] == "either"


def test_the_lowest_price_ever_says_buy_and_when_steam_allows_the_next_discount():
    now = timing.Now(1999, 5999, 67, D(2026, 10, 26))
    v = timing.verdict(joiner() + [ev("2026-10-19", 1999)], now, release=RELEASE, today=TODAY)
    assert (v["verdict"], v["summary"]) == ("buy", "This is the lowest price it has ever had on Steam.")
    assert texts(v) == [
        "−67% ($19.99) until 26 Oct.",
        "On sale in 4 of its last 4 big Steam sales, most recently −60%.",
        "Winter Sale: 17 Dec – 4 Jan 2027.",
        "Steam won't allow another discount before 25 Nov, except in a big seasonal sale.",
    ]


def test_in_a_big_sale_at_its_usual_cut_says_buy():
    events = joiner(1499, sales=PAST[:1]) + joiner(sales=PAST[1:5])[1:] + [ev("2026-10-01", 2399)]
    now = timing.Now(2399, 5999, 60, D(2026, 10, 8))
    v = timing.verdict(events, now, release=RELEASE, today=D(2026, 10, 3))
    assert (v["verdict"], v["summary"]) == ("buy", "This is its Autumn Sale price, as low as in its last big sale.")
    assert texts(v) == [
        "−60% ($23.99) until 8 Oct.",
        "Lowest ever on Steam: $14.99 (−75%), in the Summer Sale 2025.",
        "On sale in 4 of its last 4 big Steam sales, most recently −60%.",
        "The Autumn Sale ends 8 Oct.",
    ]


def test_a_shallow_discount_before_a_big_sale_says_wait():
    now = timing.Now(4799, 5999, 20, D(2026, 10, 26))
    v = timing.verdict(joiner() + [ev("2026-10-19", 4799)], now, release=RELEASE, today=TODAY)
    assert (v["verdict"], v["title"]) == ("wait", "Wait for the Winter Sale")


def test_never_discounted_in_years_means_no_need_to_wait():
    v = timing.verdict([ev("2022-05-01", 2499, 2499)], full(2499), release=D(2022, 5, 1), today=TODAY)
    assert (v["verdict"], v["title"]) == ("buy", "No need to wait")
    assert v["summary"] == "It has never been on sale on Steam, in 4 years of price records."
    assert "Not discounted in any of its last 4 big Steam sales." in texts(v)


def test_a_new_game_has_no_sale_yet():
    v = timing.verdict([ev("2026-08-20", 2999, 2999)], full(2999), release=D(2026, 8, 20), today=TODAY)
    assert (v["verdict"], v["title"]) == ("either", "No sale yet")
    assert v["summary"] == "It hasn't been discounted since it came out on 20 Aug."
    assert "Not discounted in its last big Steam sale." in texts(v)


def test_cheaper_before_but_not_soon_is_either():
    events = [ev("2024-03-01", 3999, 3999), ev("2026-06-25", 1999, 3999), ev("2026-07-09", 3999, 3999),
              ev("2026-12-17", 1999, 3999), ev("2027-01-04", 3999, 3999)]
    v = timing.verdict(events, full(3999), release=RELEASE, today=D(2027, 1, 10))
    assert (v["verdict"], v["title"]) == ("either", "Either is fine")
    assert v["summary"] == "It was −50% in December 2026, but the next big sale, the Spring Sale, isn't until 18 Mar."


def test_a_big_sale_soon_doesnt_mean_wait_for_a_game_that_often_skips_them():
    # In 2 of its last 4 big sales, like ELDEN RING: the Winter Sale may or may not bring a discount.
    v = timing.verdict(joiner(3599, sales=PAST[3:5]), full(), release=RELEASE, today=TODAY)
    assert (v["verdict"], v["title"]) == ("either", "Either is fine")
    assert v["summary"] == "It was −40% in June 2026, but it was on sale in only 2 of its last 4 big Steam sales."


def test_an_old_base_price_isnt_quoted_as_a_low():
    # Factorio was $20 in early access and has never been on sale.
    v = timing.verdict([ev("2016-02-26", 2000, 2000), ev("2024-10-15", 3500, 3500)], full(3500),
                       release=D(2016, 2, 26), today=TODAY)
    assert (v["verdict"], v["title"]) == ("buy", "No need to wait")
    assert not any(f["kind"] == "low" for f in v["facts"])


def test_small_discounts_are_either():
    # In the backtest a deeper sale still came a quarter of the time, too often for "Buy now".
    v = timing.verdict(joiner(1799, 1999), full(1999), release=RELEASE, today=TODAY)
    assert (v["verdict"], v["summary"]) == ("either", "Its discounts are small: −10% at most in the past year.")


def test_a_rarely_discounted_game_says_buy():
    v = timing.verdict([ev("2023-01-01", 1999, 1999), ev("2025-03-13", 999, 1999), ev("2025-03-20", 1999, 1999)],
                       full(1999), release=D(2023, 1, 1), today=TODAY)
    assert (v["verdict"], v["summary"]) == ("buy", "It's rarely discounted: the last sale was in March 2025.")


def monthly_sales(n, start="2025-11-03", price=2999, regular=5999):
    """*n* weeklong sales, five weeks apart, the last ending before TODAY."""
    events, day = [ev("2024-03-01", regular, regular)], D.fromisoformat(start)
    for _ in range(n):
        events += [ev(day.isoformat(), price, regular), ev((day + dt.timedelta(days=7)).isoformat(), regular, regular)]
        day += dt.timedelta(days=35)
    return events


def test_a_game_on_sale_most_months_says_wait_for_a_sale():
    # It skips the big sales but runs its own every five weeks.
    v = timing.verdict(monthly_sales(9), full(), release=RELEASE, today=D(2026, 9, 1))
    assert (v["verdict"], v["title"]) == ("wait", "Wait for a sale")
    assert v["summary"] == "It went on sale for $29.99 or less 9 times in the past year, so another sale within two months is likely."
    assert v["expected"] == {"price": 29.99, "cut": 50, "by": "2026-10-31"}


def test_seven_sales_a_year_arent_enough():
    assert timing.verdict(monthly_sales(7), full(), release=RELEASE, today=D(2026, 9, 1))["verdict"] != "wait"


def test_no_wait_for_a_sale_while_steam_blocks_the_next_one():
    # On a shallow sale for another 20 days: Steam allows no ordinary discount for 50 days.
    events = monthly_sales(9) + [ev("2026-08-31", 5399)]
    now = timing.Now(5399, 5999, 10, D(2026, 9, 21))
    assert timing.verdict(events, now, release=RELEASE, today=D(2026, 9, 1))["title"] != "Wait for a sale"


def test_without_history_it_cant_tell_but_still_gives_the_facts():
    v = timing.verdict([], timing.Now(2999, 4999, 40, D(2026, 10, 26)), release=RELEASE, today=TODAY)
    assert (v["verdict"], v["title"], v["since"]) == ("unknown", "Can't tell yet", None)
    assert texts(v) == ["−40% ($29.99) until 26 Oct.", "Winter Sale: 17 Dec – 4 Jan 2027."]


def test_a_weeklong_deal_that_touches_a_big_sale_isnt_joining_it():
    events = [ev("2024-03-01", 5999), ev("2026-09-24", 4499), ev("2026-10-02", 5999)]
    runs = timing.spans(events, TODAY)
    record = timing.big_sale_record(runs, RELEASE, [], TODAY, D(2024, 3, 1))
    assert record[0]["sale"]["name"] == "Autumn Sale" and record[0]["joined"] is False
    # It was an ordinary discount, so Steam's 30 days run from its end.
    assert timing.next_regular(timing.sale_periods(runs), full(), RELEASE, [], TODAY) == D(2026, 11, 1)


def test_only_ordinary_discounts_start_a_cooldown():
    runs = timing.spans(joiner(), TODAY, full())
    assert timing.next_regular(timing.sale_periods(runs), full(), RELEASE, [], TODAY) is None
    runs = timing.spans(joiner() + [ev("2026-10-12", 4499), ev("2026-10-19", 5999)], TODAY, full())
    assert timing.next_regular(timing.sale_periods(runs), full(), RELEASE, [], TODAY) == D(2026, 11, 18)


def test_a_new_release_waits_30_days_for_its_first_discount():
    assert timing.allowed_from(D(2026, 10, 10), [], TODAY) == D(2026, 11, 9)
    assert timing.allowed_from(RELEASE, [D(2026, 10, 12)], TODAY) == D(2026, 11, 11)


def test_time_off_sale_is_left_out():
    events = [ev("2024-03-01", 1999, 1999), {"day": D(2025, 1, 1), "price": None, "regular": None},
              ev("2025-06-01", 1999, 1999)]
    runs = timing.spans(events, TODAY, full(1999))
    assert timing.lowest(runs).price == 1999 and timing.sale_periods(runs) == []


def test_steams_own_price_wins_over_a_lagging_log():
    runs = timing.spans([ev("2024-03-01", 5999)], TODAY, timing.Now(2999, 5999, 50))
    assert (runs[-1].start, runs[-1].price) == (TODAY, 2999)


# ── One game ────────────────────────────────────────────────

@pytest.fixture
def game_inputs(monkeypatch):
    """Steam and ITAD answers for one paid game, changeable per test."""
    monkeypatch.setattr(Config, "ITAD_API_KEY", "test-key")
    monkeypatch.setattr(timing, "steam_today", lambda: TODAY)
    inputs = {
        "details": {"name": "Game", "is_free": False, "packages": [111],
                    "release_date": {"coming_soon": False, "date": "1 Mar, 2024"}},
        "price": {"currency": "USD", "initial": 5999, "final": 5999, "discount_percent": 0},
        "log": {"events": joiner(), "url": "https://isthereanydeal.com/game/game/"},
    }

    def price_overview(ids, cc, failed=None):
        if inputs["price"] == "down":
            failed.update(ids)
            return {ids[0]: None}
        return {ids[0]: inputs["price"]}

    def history(appid):
        if inputs["log"] == "down":
            raise UpstreamError("ITAD is busy")
        return inputs["log"]

    monkeypatch.setattr(steam, "appdetails", lambda appid, cc="us": inputs["details"])
    monkeypatch.setattr(steam, "price_overview", price_overview)
    monkeypatch.setattr(steam, "discount_end", lambda appid, package_id=None: 1793728800)
    monkeypatch.setattr(itad, "steam_history", history)
    return inputs


def test_assess_puts_one_game_together(game_inputs):
    v = timing.assess(1)
    assert v["verdict"] == "wait" and v["degraded"] is False
    assert v["source"] == {"name": "IsThereAnyDeal", "url": "https://isthereanydeal.com/game/game/"}


def test_assess_reads_the_sale_end_from_steam(game_inputs):
    game_inputs["price"] = {"currency": "USD", "initial": 5999, "final": 1999, "discount_percent": 67}
    v = timing.assess(1)
    assert v["facts"][0]["text"] == "−67% ($19.99) until 3 Nov."  # 1793728800 is 10 a.m. Pacific on 3 Nov


def test_assess_without_itad_is_degraded_but_still_answers(game_inputs):
    game_inputs["log"] = "down"
    v = timing.assess(1)
    assert (v["verdict"], v["source"], v["degraded"]) == ("unknown", None, True)


@pytest.mark.parametrize("change", [
    {"details": {"name": "Free", "is_free": True}},
    {"details": {"name": "Soon", "is_free": False, "release_date": {"coming_soon": True, "date": "Q1 2027"}}},
    {"price": None},
])
def test_nothing_to_judge(game_inputs, change):
    game_inputs.update(change)
    assert timing.assess(1) is None


def test_steam_being_down_is_an_error(game_inputs):
    game_inputs["price"] = "down"
    with pytest.raises(UpstreamError):
        timing.assess(1)


# ── The API and the page ────────────────────────────────────

ANSWER = {"verdict": "buy", "title": "Buy now", "summary": "Cheapest ever.", "facts": [], "expected": None,
          "next_sale": None, "since": "2024-03-01", "source": None, "degraded": False}


def test_timing_endpoint(client, monkeypatch):
    assert client.get("/api/game/1/timing").status_code == 404  # no ITAD key
    monkeypatch.setattr(Config, "ITAD_API_KEY", "test-key")
    monkeypatch.setattr(timing, "assess", lambda appid: dict(ANSWER))
    r = client.get("/api/game/1/timing")
    assert r.get_json() == {k: v for k, v in ANSWER.items() if k != "degraded"}
    assert r.headers["Cache-Control"] == "public, max-age=0, s-maxage=10800, stale-while-revalidate=86400"

    monkeypatch.setattr(timing, "assess", lambda appid: {**ANSWER, "degraded": True})
    assert "s-maxage=60" in client.get("/api/game/1/timing").headers["Cache-Control"]
    monkeypatch.setattr(timing, "assess", lambda appid: None)
    assert client.get("/api/game/1/timing").status_code == 404

    def down(appid):
        raise UpstreamError("Steam is down")

    monkeypatch.setattr(timing, "assess", down)
    assert client.get("/api/game/1/timing").status_code == 503


@pytest.fixture
def page(client, monkeypatch):
    def get(game, key="test-key"):
        monkeypatch.setattr(Config, "ITAD_API_KEY", key)
        monkeypatch.setattr(game_service, "load_game", lambda appid: game)
        return client.get(f"/game/{game['appid']}").get_data(as_text=True)

    return get


def test_the_panel_waits_for_the_verdict(page):
    html = page(make_game())
    assert "Buy now or wait?" in html and "data-timing" in html
    assert "worth waiting for a sale" not in html  # timing advice lives in the new panel only


@pytest.mark.parametrize("game, key", [
    (make_game(), ""),
    (make_game(is_free=True), "test-key"),
    (make_game(coming_soon=True), "test-key"),
    (make_game(prices={**make_game()["prices"], "regions": [], "available": False}), "test-key"),
])
def test_no_panel_without_something_to_judge(page, game, key):
    assert "data-timing" not in page(game, key)
