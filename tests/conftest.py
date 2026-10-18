import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from services.http import clear_caches  # noqa: E402

# Units per US dollar, roughly mid-October 2026.
FIXED_RATES = {
    "USD": 1.0, "CAD": 1.4148, "MXN": 18.47, "BRL": 5.089, "GBP": 0.7508, "EUR": 0.8897,
    "PLN": 3.886, "INR": 95.08, "CNY": 6.702, "JPY": 158.0, "KRW": 1334.6, "IDR": 18015.0,
    "AUD": 1.44, "AED": 3.6725,
}


@pytest.fixture(autouse=True)
def _fresh_caches():
    clear_caches()
    yield
    clear_caches()


@pytest.fixture
def fixed_fx(monkeypatch):
    from services import fx

    monkeypatch.setattr(
        fx, "rates", lambda: {"rates": FIXED_RATES, "updated": 1792281751, "source": "test", "live": False}
    )
    return FIXED_RATES


@pytest.fixture
def client():
    from app import app

    app.config["TESTING"] = True
    return app.test_client()


def make_game(**overrides):
    """A complete game bundle, as services.game.load_game returns it."""
    game = {
        "appid": 1086940,
        "name": "Baldur's Gate 3",
        "details": {"name": "Baldur's Gate 3", "is_free": False},
        "is_free": False,
        "coming_soon": False,
        "header_image": "https://example.test/header.jpg",
        "store_url": "https://store.steampowered.com/app/1086940",
        "prices": {
            "is_free": False, "available": True, "us_usd": 59.99, "reference_usd": 59.99,
            "cheapest": "IN", "priciest": "GB", "fx": {"live": False, "source": "test", "updated": 0},
            "regions": [
                {"cc": "us", "code": "US", "name": "United States", "available": True, "error": False,
                 "currency": "USD", "final": 59.99, "initial": 59.99, "discount": 0,
                 "final_formatted": "$59.99", "initial_formatted": "", "usd": 59.99, "vs_us_pct": 0.0,
                 "usd_priced": False, "is_cheapest": False, "is_priciest": False},
                {"cc": "in", "code": "IN", "name": "India", "available": True, "error": False,
                 "currency": "INR", "final": 1999.0, "initial": 1999.0, "discount": 0,
                 "final_formatted": "₹ 1,999", "initial_formatted": "", "usd": 21.02, "vs_us_pct": -65.0,
                 "usd_priced": False, "is_cheapest": True, "is_priciest": False},
            ],
        },
        "usd_price": 59.99,
        "reviews": {
            "available": True, "positivity": 0.96, "total_reviews": 650000, "total_positive": 624000,
            "total_negative": 26000, "review_label": "Overwhelmingly Positive", "p50_all": 120.0,
            "p50_recent": 60.0, "p75_recent": 140.0, "recent_positivity": 0.93, "recent_count": 100,
        },
        "value": {"score": 88, "label": "Excellent value", "breakdown": {
            "effective_hours": 120.0, "hours_per_dollar": 2.0, "cost_per_hour": 0.5,
            "playtime_pts": 33.0, "positivity_pts": 31.5, "confidence_pts": 10.0, "is_free": False}},
        "vibes": {"top": []},
    }
    game.update(overrides)
    return game
