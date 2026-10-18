import pytest

from config import Config
from services import pricing, steam
from services.http import UpstreamError

# What Steam returned for ELDEN RING on 2026-10-18 (subset of regions).
ELDEN = {
    "us": ("USD", 5999, "$59.99"),
    "de": ("EUR", 5999, "59,99€"),
    "in": ("INR", 359900, "₹ 3,599"),
    "tr": ("USD", 3999, "$39.99 USD"),
    "jp": ("JPY", 902000, "¥ 9,020"),
    "ae": ("AED", 24900, "249.00 AED"),
}


@pytest.fixture
def fake_prices(monkeypatch, fixed_fx):
    def fake(appids, cc):
        if cc == "kr":
            raise UpstreamError("rate limited")
        if cc not in ELDEN:
            return {a: None for a in appids}
        cur, cents, fmt = ELDEN[cc]
        po = {"currency": cur, "initial": cents, "final": cents, "discount_percent": 0,
              "final_formatted": fmt, "initial_formatted": ""}
        return {a: po for a in appids}

    monkeypatch.setattr(steam, "price_overview", fake)


def by_code(result):
    return {r["code"]: r for r in result["regions"]}


def test_regions_cover_config_and_euro_area_uses_german_store():
    eu = Config.region("eu")
    assert eu["cc"] == "de" and eu["code"] == "EU"
    assert len({r["cc"] for r in Config.REGIONS}) == len(Config.REGIONS)


def test_regional_prices_convert_with_live_rates(fake_prices):
    res = pricing.regional_prices(1245620)
    rows = by_code(res)
    assert res["us_usd"] == 59.99
    assert rows["EU"]["final_formatted"] == "59,99€"
    assert rows["EU"]["usd"] == round(59.99 / 0.8897, 2)
    assert rows["IN"]["usd"] == round(3599 / 95.08, 2)
    assert rows["JP"]["final"] == 9020.0
    assert rows["IN"]["vs_us_pct"] == round((rows["IN"]["usd"] - 59.99) / 59.99 * 100, 1)


def test_cheapest_priciest_and_usd_tier(fake_prices):
    res = pricing.regional_prices(1245620)
    rows = by_code(res)
    assert res["cheapest"] == "IN" and rows["IN"]["is_cheapest"]
    assert res["priciest"] == "AE" and rows["AE"]["is_priciest"]
    assert rows["TR"]["usd_priced"] is True and rows["US"]["usd_priced"] is False


def test_unsold_and_failed_regions_are_told_apart(fake_prices):
    rows = by_code(pricing.regional_prices(1245620))
    assert rows["GB"]["available"] is False and rows["GB"]["error"] is False
    assert rows["KR"]["available"] is False and rows["KR"]["error"] is True


def test_free_games_skip_price_lookups(monkeypatch, fixed_fx):
    monkeypatch.setattr(steam, "price_overview", lambda *a: pytest.fail("should not be called"))
    res = pricing.regional_prices(730, is_free=True)
    assert res["is_free"] and res["regions"] == [] and res["reference_usd"] == 0.0


def test_reference_price_falls_back_to_median_without_us(monkeypatch, fixed_fx):
    def fake(appids, cc):
        po = {"in": {"currency": "INR", "initial": 95080, "final": 95080, "discount_percent": 0},
              "jp": {"currency": "JPY", "initial": 316000, "final": 316000, "discount_percent": 0}}.get(cc)
        return {a: po for a in appids}

    monkeypatch.setattr(steam, "price_overview", fake)
    res = pricing.regional_prices(1)
    assert res["us_usd"] is None
    assert res["reference_usd"] == pytest.approx(15.0, abs=0.01)  # median of $10 and $20
