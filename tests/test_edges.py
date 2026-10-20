import pytest

from venue_arb import edges
from venue_arb.edges import fill
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


def test_score_applies_pair_fees():
    p = ExactPair("K", "P", "tok", "yes", None, "KXS", {"rate": 0.05, "exponent": 1}, True, 0.9, False)
    out = edges.score(p, [L("yes", 0.40, 100)], [L("no", 0.55, 100)], NOFEE)
    assert out[0].best_edge == pytest.approx(0.05 - 0.05 * 0.55 * 0.45)


def test_fill_single_level():
    k = [Level("yes", 1, 0.41, 120)]; pm = [Level("no", 1, 0.58, 400)]
    f = fill(k, pm, lambda p, q: 0.017 * q, lambda p, q: 0.0, 100)
    assert f.filled == 100 and not f.insufficient_depth
    assert f.k_avg_price == 0.41 and f.pm_avg_price == 0.58
    assert f.fees_usd == pytest.approx(1.7) and f.cost_usd == pytest.approx(100.7)
    assert f.net_cents == pytest.approx(-0.7) and f.net_bps == pytest.approx(-0.7 / 100.7 * 1e4)


def test_fill_walks_through_negative_slices():
    k = [Level("yes", 1, 0.41, 120), Level("yes", 2, 0.45, 300)]; pm = [Level("no", 1, 0.58, 400)]
    f = fill(k, pm, lambda p, q: 0.0, lambda p, q: 0.0, 400)
    assert f.filled == 400 and f.k_avg_price == pytest.approx((120 * 0.41 + 280 * 0.45) / 400)


def test_fill_insufficient_depth():
    f = fill([Level("yes", 1, 0.41, 120)], [Level("no", 1, 0.58, 400)], lambda p, q: 0.0, lambda p, q: 0.0, 1000)
    assert f.filled == 120 and f.insufficient_depth and f.requested == 1000


def test_fill_empty_ladder_and_bad_size():
    assert fill([], [Level("no", 1, 0.58, 400)], lambda p, q: 0.0, lambda p, q: 0.0, 10).filled == 0
    with pytest.raises(ValueError):
        fill([], [], lambda p, q: 0.0, lambda p, q: 0.0, 0)


def test_fill_fees_use_each_slices_own_count():
    from math import ceil

    fk = lambda p, q: ceil(100 * 0.07 * q * p * (1 - p)) / 100  # nonlinear: cent rounding per slice
    k = [Level("yes", 1, 0.41, 120), Level("yes", 2, 0.45, 300)]; pm = [Level("no", 1, 0.58, 400)]
    f = fill(k, pm, fk, zero, 250)  # slices: 120 @ 0.41, then 130 @ 0.45 (cut)
    assert f.filled == 250
    assert f.fees_usd == pytest.approx(fk(0.41, 120) + fk(0.45, 130))
    assert f.fees_usd != pytest.approx(fk(0.41, 250))
