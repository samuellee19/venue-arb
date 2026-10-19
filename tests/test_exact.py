from datetime import UTC, datetime

from venue_arb import exact


def row(**kw):
    base = {
        "kalshi_id": "K1", "pm_id": "P1", "pm_yes_token": "tok", "relation": "exact", "yes_maps_to": "yes",
        "confidence": 0.85, "label": None, "kalshi_close": datetime(2026, 11, 1, tzinfo=UTC),
        "pm_close": datetime(2026, 11, 3, tzinfo=UTC), "kalshi_series": "KXS",
        "pm_fee_schedule": {"rate": 0.05}, "pm_fees_enabled": True,
    }
    return base | kw


def test_exact_unreviewed_high_confidence_included():
    [p] = exact.eligible([row()])
    assert (p.kalshi_id, p.pm_id, p.reviewed, p.kalshi_series) == ("K1", "P1", False, "KXS")
    assert p.close_time == datetime(2026, 11, 3, tzinfo=UTC)  # later leg


def test_exact_low_confidence_needs_review():
    assert exact.eligible([row(confidence=0.7)]) == []
    [p] = exact.eligible([row(confidence=0.7, label="correct")])
    assert p.reviewed


def test_wrong_review_excludes():
    assert exact.eligible([row(confidence=0.99, label="wrong")]) == []
    assert exact.eligible([row(confidence=0.99, label="unsure")]) == []


def test_near_excluded():
    assert exact.eligible([row(relation="near", label="correct")]) == []
