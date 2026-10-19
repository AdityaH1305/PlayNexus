import datetime as dt

import psycopg
import pytest

from config import Config
from conftest import make_game
from services import deals, history, pool, steam
from services import game as game_service
from services.http import UpstreamError

TODAY = dt.date(2026, 10, 19)
DAY = dt.timedelta(days=1)


class FakeResult:
    def __init__(self, rows):
        self._rows = rows

    def fetchall(self):
        return self._rows


class FakeDB:
    """Stands in for Postgres: answers services.history's own statements from
    a dict of rows, so reads and writes can be checked without a server."""

    def __init__(self):
        self.rows = {}  # (appid, cc, day) -> (final, initial, discount, currency)
        self.statements = []
        self.connect_error = None  # raised on connect: the database is down
        self.query_error = None  # raised on execute

    def connect(self, timeout=5):
        if self.connect_error:
            raise self.connect_error
        return FakeConnection(self)

    def seed(self, appid, start, prices):
        """One row a day from *start*; each price is final cents or (final, initial, discount)."""
        for i, p in enumerate(prices):
            final, initial, discount = p if isinstance(p, tuple) else (p, p, 0)
            self.rows[(appid, "us", start + i * DAY)] = (final, initial, discount, "USD")


class FakeConnection:
    def __init__(self, db):
        self.db = db

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def execute(self, sql, params=None):
        db = self.db
        if db.query_error:
            raise db.query_error
        db.statements.append(sql)
        if sql == history.UPSERT:
            for appid, *vals in zip(params["appids"], params["finals"], params["initials"],
                                    params["discounts"], params["currencies"]):
                db.rows[(appid, params["cc"], params["day"])] = tuple(vals)
            return FakeResult([])
        if sql == history.POINTS:
            return FakeResult(sorted(
                (day, *vals[:3]) for (appid, cc, day), vals in db.rows.items()
                if appid == params["appid"] and cc == params["cc"] and day >= params["start"]
            ))
        if sql == history.LOWS:
            by_app = {}
            for (appid, cc, day), vals in db.rows.items():
                if cc == params["cc"] and appid in params["ids"]:
                    by_app.setdefault(appid, []).append((day, vals[0]))
            out = []
            for appid, items in by_app.items():
                recent = [final for day, final in items if day >= params["start"]]
                out.append((appid, min(recent) if recent else None, min(day for day, _ in items)))
            return FakeResult(out)
        return FakeResult([])  # the schema


@pytest.fixture
def db(monkeypatch):
    fake = FakeDB()
    monkeypatch.setattr(Config, "DATABASE_URL", "postgresql://user:pw@db.example.test/neondb")
    monkeypatch.setattr(history, "_connect", fake.connect)
    monkeypatch.setattr(history, "today", lambda: TODAY)
    return fake


@pytest.fixture
def live(monkeypatch):
    """Today's US prices (cents) as Steam's price_overview would give them."""
    prices = {}

    def fake(ids, cc, *, failed=None):
        return {a: ({"currency": "USD", "initial": prices[a], "final": prices[a], "discount_percent": 0}
                    if a in prices else None) for a in ids}

    monkeypatch.setattr(steam, "price_overview", fake)
    return prices


@pytest.fixture
def stub_game(monkeypatch):
    holder = {"game": make_game()}
    monkeypatch.setattr(game_service, "load_game", lambda appid: {**holder["game"], "appid": appid})
    return holder


def deal_row(appid, price):
    return {
        "appid": appid, "name": f"Game {appid}", "header": None, "capsule": None, "tags": [],
        "sale_price": price, "normal_price": price * 2, "savings": 50, "review_pct": 90,
        "review_count": 5000, "review_label": "Very Positive", "deal_score": 70, "deal_label": "Good deal",
        "url": "https://www.cheapshark.com/redirect?dealID=x", "release_date": 0, "value": None,
    }


# ── Without a database ────────────────────────────────────

def test_everything_is_off_without_a_database(client, stub_game, monkeypatch):
    def no_calls(*a, **k):
        raise AssertionError("no database calls without DATABASE_URL")

    monkeypatch.setattr(history, "_connect", no_calls)
    assert not history.enabled()
    assert history.game_history(1)["points"] == []
    assert history.lows([1, 2]) == {} and history.at_low([deal_row(1, 1.0)]) == set()

    html = client.get("/game/1086940").get_data(as_text=True)
    assert "data-price-history" not in html and "data-price-low" not in html
    r = client.get("/api/game/1086940/history")
    assert r.status_code == 200 and r.get_json()["points"] == [] and r.get_json()["status"] is None


def test_game_page_leaves_room_for_history_with_a_database(client, stub_game, db):
    html = client.get("/game/1086940").get_data(as_text=True)
    assert "data-price-history-panel hidden" in html and "data-price-low hidden" in html

    stub_game["game"] = make_game(is_free=True, usd_price=0.0)
    html = client.get("/game/730").get_data(as_text=True)
    assert "data-price-history" not in html and "data-price-low" not in html


# ── Daily snapshot ────────────────────────────────────────

def test_snapshot_saves_pool_and_top_sellers_once_a_day(db, monkeypatch):
    games = [{"appid": a, "is_free": False} for a in range(1, 121)] + [{"appid": 999, "is_free": True}]
    monkeypatch.setattr(pool, "games", lambda: games)
    monkeypatch.setattr(steam, "weekly_top_sellers", lambda cc, count: {"ranks": [
        {"appid": 5}, {"appid": 500}, {"appid": None}, {"appid": 501}]})
    final = {500: 999}
    calls = []

    def price_overview(ids, cc, *, failed=None):
        calls.append((list(ids), cc))
        return {a: None if a == 501 else {  # 501 isn't sold in the US
            "currency": "USD", "initial": 1999, "final": final.get(a, 1999),
            "discount_percent": 50 if final.get(a, 1999) < 1999 else 0} for a in ids}

    monkeypatch.setattr(steam, "price_overview", price_overview)

    result = history.snapshot()
    assert result["day"] == "2026-10-19"
    assert (result["tracked"], result["saved"], result["not_sold"], result["failed"]) == (122, 121, 1, 0)
    assert all(len(ids) <= 50 and cc == "us" for ids, cc in calls)
    assert 999 not in {a for ids, _ in calls for a in ids}  # free games aren't priced
    assert db.statements == [history.SCHEMA, history.UPSERT]  # one write for the whole day
    assert db.rows[(500, "us", TODAY)] == (999, 1999, 50, "USD")

    # Running it again the same day refreshes today's rows instead of adding more.
    final[1] = 1499
    history.snapshot()
    assert len(db.rows) == 121
    assert db.rows[(1, "us", TODAY)] == (1499, 1999, 50, "USD")
    assert "ON CONFLICT (appid, cc, day) DO UPDATE" in history.UPSERT


def test_snapshot_retries_batches_steam_dropped(db, monkeypatch):
    monkeypatch.setattr(pool, "games", lambda: [{"appid": a, "is_free": False} for a in range(1, 61)])
    monkeypatch.setattr(steam, "weekly_top_sellers", lambda cc, count: {})
    monkeypatch.setattr(history.time, "sleep", lambda s: None)
    dropped = []

    def price_overview(ids, cc, *, failed=None):
        if 1 in ids and not dropped:
            dropped.append(list(ids))
            failed.update(ids)
            return {a: None for a in ids}
        return {a: {"currency": "USD", "initial": 999, "final": 999} for a in ids}

    monkeypatch.setattr(steam, "price_overview", price_overview)
    result = history.snapshot()
    assert len(dropped[0]) == 50
    assert (result["saved"], result["failed"]) == (60, 0)


def test_snapshot_saves_nothing_when_steam_is_down(db, monkeypatch):
    monkeypatch.setattr(pool, "games", lambda: [{"appid": a, "is_free": False} for a in range(1, 11)])
    monkeypatch.setattr(steam, "weekly_top_sellers", lambda cc, count: {})
    monkeypatch.setattr(history.time, "sleep", lambda s: None)

    def down(ids, cc, *, failed=None):
        failed.update(ids)
        return {a: None for a in ids}

    monkeypatch.setattr(steam, "price_overview", down)
    with pytest.raises(UpstreamError):
        history.snapshot()
    assert history.UPSERT not in db.statements


def test_cron_needs_the_secret(client, db, monkeypatch):
    monkeypatch.setattr(history, "snapshot", lambda: {"day": "2026-10-19", "saved": 3})
    # No secret configured: nobody gets in, even with an empty bearer token.
    assert client.get("/api/cron/snapshot", headers={"Authorization": "Bearer "}).status_code == 401

    monkeypatch.setattr(Config, "CRON_SECRET", "s3cret-value-1234")
    assert client.get("/api/cron/snapshot").status_code == 401
    assert client.get("/api/cron/snapshot", headers={"Authorization": "Bearer nope"}).status_code == 401
    r = client.get("/api/cron/snapshot", headers={"Authorization": "Bearer s3cret-value-1234"})
    assert r.status_code == 200 and r.get_json()["saved"] == 3
    assert r.headers["Cache-Control"] == "private, no-store"


def test_cron_reports_why_nothing_was_saved(client, monkeypatch):
    monkeypatch.setattr(Config, "CRON_SECRET", "s3cret-value-1234")
    auth = {"Authorization": "Bearer s3cret-value-1234"}
    r = client.get("/api/cron/snapshot", headers=auth)
    assert r.status_code == 503 and "DATABASE_URL" in r.get_json()["error"]

    monkeypatch.setattr(Config, "DATABASE_URL", "postgresql://db.example.test/neondb")
    for exc, message in ((history.HistoryError("db down"), "database"), (UpstreamError("steam"), "Steam")):
        def fail(exc=exc):
            raise exc

        monkeypatch.setattr(history, "snapshot", fail)
        r = client.get("/api/cron/snapshot", headers=auth)
        assert r.status_code == 503 and message in r.get_json()["error"]


# ── Reading it back ───────────────────────────────────────

def test_price_status():
    assert history.price_status(None, 1000, 2000) is None
    assert history.price_status(900, 1000, 2000) == "low"
    assert history.price_status(1000, 1000, 2000) == "low"
    assert history.price_status(1000, 1000, 1000) == "flat"
    assert history.price_status(1100, 1000, 2000) == "near"
    assert history.price_status(1101, 1000, 2000) is None


def test_history_endpoint_points_low_and_status(client, db, live):
    # 120 days at $59.99, with an old $19.99 sale (outside the 90 days) and a
    # recent half-price one.
    prices = [5999] * 120
    prices[15:21] = [1999] * 6
    prices[90:96] = [(2999, 5999, 50)] * 6
    db.seed(10, TODAY - 119 * DAY, prices)
    live[10] = 2999  # half price again today

    r = client.get("/api/game/10/history")
    assert "s-maxage=3600" in r.headers["Cache-Control"]
    j = r.get_json()
    assert len(j["points"]) == 120
    assert j["points"][0] == {"day": "2026-06-22", "final": 5999, "initial": 5999, "discount": 0}
    assert j["points"][95] == {"day": "2026-09-25", "final": 2999, "initial": 5999, "discount": 50}
    assert j["low"] == {"final": 2999, "day": "2026-09-25", "full": True, "since": "2026-07-22"}
    assert (j["now"], j["status"]) == (2999, "low")


def test_low_before_90_days_counts_from_the_first_snapshot(db, live):
    db.seed(10, TODAY - 9 * DAY, [5999] * 10)
    h = history.game_history(10)  # Steam has no live price: last night's is used
    assert h["low"] == {"final": 5999, "day": "2026-10-19", "full": False, "since": "2026-10-10"}
    assert (h["now"], h["status"]) == (5999, "flat")
    assert history.lows([10]) == {}  # too soon to call anything a 90-day low


def test_history_survives_a_database_outage(client, db, live):
    db.connect_error = psycopg.OperationalError("connection refused")
    r = client.get("/api/game/10/history")
    assert r.status_code == 200 and r.get_json()["points"] == []
    assert "s-maxage=60" in r.headers["Cache-Control"]  # degraded: kept a minute
    assert history.lows([10]) == {}


def test_no_table_yet_reads_as_no_history(client, db, live):
    db.query_error = psycopg.errors.UndefinedTable('relation "price_snapshots" does not exist')
    r = client.get("/api/game/10/history")
    assert r.get_json()["points"] == []
    assert "s-maxage=3600" in r.headers["Cache-Control"]  # not an outage


def test_at_low_compares_in_cents(db):
    db.seed(7, TODAY - 99 * DAY, [1999] * 100)
    assert history.at_low([deal_row(7, 19.99)]) == {7}
    assert history.at_low([deal_row(7, 20.49)]) == set()


def test_deals_page_flags_90_day_lows(client, db, monkeypatch):
    rows = [deal_row(1, 3.99), deal_row(2, 19.99), deal_row(3, 9.99)]
    monkeypatch.setattr(deals, "current_deals", lambda pages=2: rows)
    db.seed(1, TODAY - 99 * DAY, [799] * 99 + [399])  # today's sale is the lowest yet
    db.seed(2, TODAY - 99 * DAY, [2999] * 50 + [1499] * 5 + [2999] * 44 + [1999])  # was cheaper last month
    db.seed(3, TODAY - 19 * DAY, [999] * 20)  # only tracked for 20 days

    html = client.get("/deals").get_data(as_text=True)
    flagged = [row for row in html.split("<tr>") if "deal-low" in row]
    assert len(flagged) == 1 and "Game 1<" in flagged[0]
    assert "90-day low: the US sale price" in html

    monkeypatch.setattr(Config, "DATABASE_URL", "")
    html = client.get("/deals").get_data(as_text=True)
    assert "deal-low" not in html and "90-day low:" not in html
