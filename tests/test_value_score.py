import pytest

from services.value_score import compute_value_score, label_for, worth_it_price

LOVED_LONG = {"p50_all": 60.0, "p50_recent": 30.0, "p75_recent": 80.0, "positivity": 0.95, "total_reviews": 200000}
SHORT_MEH = {"p50_all": 3.0, "p50_recent": 2.0, "p75_recent": 4.0, "positivity": 0.70, "total_reviews": 800}


def test_unknown_price_is_not_scored_as_free():
    score, label, breakdown = compute_value_score(None, LOVED_LONG)
    assert score is None and label == "No price yet"
    assert breakdown["cost_per_hour"] is None


def test_free_games_get_the_original_floor():
    score, _, _ = compute_value_score(0.0, LOVED_LONG, is_free=True)
    pos_norm = (0.95 - 0.60) / 0.40
    assert score >= round(100 * (0.60 + 0.30 * pos_norm + 0.10 * 1.0))


def test_score_falls_as_price_rises():
    scores = [compute_value_score(p, LOVED_LONG)[0] for p in (5, 20, 60, 120)]
    assert scores == sorted(scores, reverse=True)


def test_breakdown_is_in_points_and_sums_to_score():
    score, _, b = compute_value_score(19.99, LOVED_LONG)
    assert b["playtime_pts"] <= 55 and b["positivity_pts"] <= 35 and b["confidence_pts"] <= 10
    assert abs((b["playtime_pts"] + b["positivity_pts"] + b["confidence_pts"]) - score) <= 1
    # Effective hours = max(median all 60, 0.7 × p75 recent 56, median recent 30).
    assert b["effective_hours"] == 60.0
    assert b["cost_per_hour"] == round(19.99 / 60, 2)


@pytest.mark.parametrize("target", [55, 70, 85])
def test_worth_it_price_is_the_highest_price_meeting_the_target(target):
    price = worth_it_price(LOVED_LONG, target)
    assert price is not None
    assert compute_value_score(price, LOVED_LONG)[0] >= target
    if price < 200:
        assert compute_value_score(price + 0.5, LOVED_LONG)[0] < target + 1


def test_worth_it_price_none_when_unreachable():
    assert worth_it_price(SHORT_MEH, 85) is None


def test_labels():
    assert [label_for(s) for s in (90, 72, 60, 45, 10)] == [
        "Excellent value", "Great value", "Good value", "Fair value", "Poor value"]
