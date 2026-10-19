import pytest

from venue_arb import edges
from venue_arb.exact import ExactPair
from venue_arb.models import Level


def L(side, price, size, i=0):
    return Level(side, i, price, size)


def zero(p, q):
    return 0.0


def pair(maps="yes"):
    return ExactPair("K", "P", "tok", maps, None, "KXS", None, False, 0.9, False)


def test_walk_single_level():
    best, cap, usd, avg = edges.walk([L("yes", 0.40, 500)], [L("no", 0.55, 300)], zero, zero)
    assert best == pytest.approx(0.05) and cap == 300 and usd == pytest.approx(285.0)
    assert avg == pytest.approx(0.05)


def test_walk_stops_at_min_edge():
    pm = [L("no", 0.55, 100, 0), L("no", 0.595, 100, 1)]
    _, cap, _, _ = edges.walk([L("yes", 0.40, 500)], pm, zero, zero)
    assert cap == 100


def test_edge_empty_side_has_no_capacity():
    best, cap, usd, avg = edges.walk([L("yes", 0.40, 500)], [], zero, zero)
    assert best is None and cap == 0 and usd == 0 and avg is None


def test_directions_swap_when_yes_maps_to_no():
    k = [L("yes", 0.40, 100), L("no", 0.90, 100)]
    pm = [L("yes", 0.55, 100), L("no", 0.99, 100)]
    out = {e.direction: e for e in edges.score(pair("no"), k, pm, {"fee_type": "quadratic", "fee_multiplier": 0})}
    assert out["k_yes_pm_no"].best_edge == pytest.approx(0.05)
    assert out["k_no_pm_yes"].best_edge < 0


def test_fees_reduce_edge():
    from venue_arb.fees import kalshi_taker_fee, polymarket_taker_fee

    fk = lambda p, q: kalshi_taker_fee(p, q, "quadratic", 1.0)
    fp = lambda p, q: polymarket_taker_fee(p, q, {"rate": 0.05, "exponent": 1}, True)
    best, *_ = edges.walk([L("yes", 0.40, 100)], [L("no", 0.55, 100)], fk, fp)
    assert best == pytest.approx(0.05 - 0.029175)


NOFEE = {"fee_type": "quadratic", "fee_multiplier": 0}


def test_leg_mapping_yes_maps_to_yes():
    k = [L("yes", 0.40, 100), L("no", 0.90, 100)]
    pm = [L("no", 0.55, 100), L("yes", 0.99, 100)]
    out = {e.direction: e for e in edges.score(pair("yes"), k, pm, NOFEE)}
    assert out["k_yes_pm_no"].best_edge == pytest.approx(0.05)  # Kalshi yes + PM no
    assert out["k_no_pm_yes"].best_edge < 0
    k = [L("yes", 0.90, 100), L("no", 0.40, 100)]
    pm = [L("yes", 0.55, 100), L("no", 0.99, 100)]
    out = {e.direction: e for e in edges.score(pair("yes"), k, pm, NOFEE)}
    assert out["k_no_pm_yes"].best_edge == pytest.approx(0.05)  # Kalshi no + PM yes
    assert out["k_yes_pm_no"].best_edge < 0


def test_leg_mapping_yes_maps_to_no():
    k = [L("yes", 0.90, 100), L("no", 0.40, 100)]
    pm = [L("no", 0.55, 100), L("yes", 0.99, 100)]
    out = {e.direction: e for e in edges.score(pair("no"), k, pm, NOFEE)}
    assert out["k_no_pm_yes"].best_edge == pytest.approx(0.05)  # Kalshi no + PM no
    assert out["k_yes_pm_no"].best_edge < 0


def test_walk_multi_level_partial_carry():
    pm = [L("no", 0.55, 100, 0), L("no", 0.56, 150, 1), L("no", 0.60, 1000, 2)]
    best, cap, usd, avg = edges.walk([L("yes", 0.40, 500)], pm, zero, zero)
    assert best == pytest.approx(0.05) and cap == 250 and usd == pytest.approx(239.0)
    assert avg == pytest.approx(0.044)
