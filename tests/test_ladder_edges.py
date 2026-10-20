import itertools

import pytest

from venue_arb import ladder_edges as le
from venue_arb.models import Level


def L(side, price, size, i=0):
    return Level(side, i, price, size)


NOFEE = {"fee_type": "quadratic", "fee_multiplier": 0}


def payout(direction, a, b, brti, binance):
    """Dollars paid at settlement by one contract of each leg. Kalshi strike a: BRTI > a; PM strike b: Binance > b."""
    k_yes, pm_yes = brti > a, binance > b
    if direction == "k_yes_pm_no":
        return int(k_yes) + int(not pm_yes)
    return int(pm_yes) + int(not k_yes)


def frange(lo, hi, n):
    return [lo + (hi - lo) * i / n for i in range(n + 1)]


def states(a, b, up, down, n=60):
    """BRTI over [min(a,b) - 3w, max(a,b) + 3w], diff = BRTI - Binance over [-down, up], w = max(up, down).
    Grids include the exact boundary points a, b, a +/- 0.01 and the bound edges."""
    w = max(up, down, 1.0)
    brtis = frange(min(a, b) - 3 * w, max(a, b) + 3 * w, n) + [a, a - 0.01, a + 0.01, b, b - 0.01, b + 0.01]
    diffs = frange(-down, up, 20) + [0.0]
    for brti, d in itertools.product(brtis, diffs):
        yield brti, brti - d


def test_spread_requires_basis_gap():
    # up = 182 (BRTI can exceed Binance by 182), down = 152 (Binance can exceed BRTI by 152)
    assert le.admissible(80_000, 80_152, 182, 152) == ["k_yes_pm_no"]  # b - a = down: just enough
    assert le.admissible(80_000, 80_151, 182, 152) == []  # one dollar short
    assert le.admissible(80_182, 80_000, 182, 152) == ["pm_yes_k_no"]  # a - b = up
    assert le.admissible(80_181, 80_000, 182, 152) == []
    assert le.admissible(80_000, 80_000, 182, 152) == []  # same strike carries basis exposure
    assert le.admissible(80_000, 80_000, 0, 0) == ["k_yes_pm_no", "pm_yes_k_no"]  # zero basis: both hold


def test_directional_bounds_are_not_symmetric():
    # a systematic basis (BRTI below Binance) only constrains the direction it can break
    assert le.admissible(100, 110, 0, 10) == ["k_yes_pm_no"]
    assert le.admissible(110, 100, 0, 10) == ["pm_yes_k_no"]  # up = 0: a - b = 10 >= 0
    assert le.admissible(100, 101, 50, 0) == ["k_yes_pm_no"]  # down = 0: any b >= a works


@pytest.mark.parametrize("up,down", [(182, 152), (6, 7), (0, 10), (10, 0), (0, 0), (0.5, 0.25)])
def test_admissible_pairs_pay_in_every_state(up, down):
    step = max(up, down, 1.0) / 4  # strike gaps from 0 to 6x the bound, including exactly up and down
    strikes = [80_000 + step * k for k in range(-12, 13)] + [80_000 - up, 80_000 + down, 79_999.99]
    checked = 0
    for a, b in itertools.product(strikes, strikes):
        for direction in le.admissible(a, b, up, down):
            for brti, binance in states(a, b, up, down):
                assert payout(direction, a, b, brti, binance) >= 1, (direction, a, b, brti, binance)
            checked += 1
    assert checked > 0


def test_inadmissible_pair_fails_in_some_state():
    up, down = 182, 152
    a, b = 80_000, 80_100  # b - a = 100 < down
    assert "k_yes_pm_no" not in le.admissible(a, b, up, down)
    worst = min(payout("k_yes_pm_no", a, b, brti, bn) for brti, bn in states(a, b, up, down))
    assert worst == 0  # e.g. BRTI = a, Binance = a + 152 > b: both legs lose
    a, b = 80_100, 80_000  # a - b = 100 < up
    assert "pm_yes_k_no" not in le.admissible(a, b, up, down)
    assert min(payout("pm_yes_k_no", a, b, brti, bn) for brti, bn in states(a, b, up, down)) == 0


def test_near_exact():
    assert le.is_near_exact(81_499.99, 81_500)  # Kalshi T81499.99 vs PM above 81500
    assert le.is_near_exact(81_500, 81_500)
    assert le.is_near_exact(81_501, 81_500)  # exactly the $1 tolerance
    assert not le.is_near_exact(81_000, 81_500)


def test_score_hand_computed_two_level_walk():
    # k_yes_pm_no buys Kalshi yes asks and PM no asks
    k = [L("yes", 0.40, 100, 0), L("yes", 0.42, 200, 1), L("no", 0.10, 999)]
    pm = [L("no", 0.50, 150, 0), L("no", 0.53, 500, 1), L("yes", 0.10, 999)]
    e = le.score_spread("k_yes_pm_no", k, pm, NOFEE, None, False)
    # slices: 100 @ (0.40, 0.50) edge 0.10; 50 @ (0.42, 0.50) edge 0.08; 150 @ (0.42, 0.53) edge 0.05
    assert e.direction == "k_yes_pm_no"
    assert e.best_edge == pytest.approx(0.10)
    assert e.capacity_contracts == pytest.approx(300)
    assert e.capacity_usd == pytest.approx(100 * 0.90 + 50 * 0.92 + 150 * 0.95)
    assert e.avg_edge == pytest.approx((100 * 0.10 + 50 * 0.08 + 150 * 0.05) / 300)


def test_score_other_direction_uses_pm_yes_and_kalshi_no():
    k = [L("yes", 0.90, 100), L("no", 0.40, 100)]
    pm = [L("yes", 0.55, 100), L("no", 0.90, 100)]
    e = le.score_spread("pm_yes_k_no", k, pm, NOFEE, None, False)
    assert e.direction == "pm_yes_k_no" and e.best_edge == pytest.approx(0.05) and e.capacity_contracts == 100


def test_score_applies_plan2_fees():
    from venue_arb.fees import kalshi_taker_fee, polymarket_taker_fee

    sched = {"rate": 0.05, "exponent": 1}
    series = {"fee_type": "quadratic", "fee_multiplier": 1.0}
    k, pm = [L("yes", 0.40, 100)], [L("no", 0.55, 100)]
    e = le.score_spread("k_yes_pm_no", k, pm, series, sched, True)
    fee = (kalshi_taker_fee(0.40, 100, "quadratic", 1.0) + polymarket_taker_fee(0.55, 100, sched, True)) / 100
    assert e.best_edge == pytest.approx(0.05 - fee) and fee == pytest.approx(0.029175)
    # fees disabled on the PM market: only Kalshi's fee
    e = le.score_spread("k_yes_pm_no", k, pm, series, sched, False)
    assert e.best_edge == pytest.approx(0.05 - 0.0168)


def test_score_rejects_unknown_direction():
    with pytest.raises(ValueError):
        le.score_spread("k_no_pm_yes", [], [], NOFEE, None, False)


def test_classify():
    assert le.classify(80_000, 80_152, 182, 152) == ("arb", ["k_yes_pm_no"])
    assert le.classify(81_499.99, 81_500, 182, 152) == ("near_exact", ["k_yes_pm_no", "pm_yes_k_no"])
    assert le.classify(81_400, 81_500, 182, 152) is None  # cross-strike inside the basis band, not same strike
    # Ruling 8: same strike is near-exact before admissibility, even when a zero basis would admit it
    assert le.classify(81_500, 81_500, 0, 0) == ("near_exact", ["k_yes_pm_no", "pm_yes_k_no"])
    assert le.classify(81_499.99, 81_500, 0, 0.01)[0] == "near_exact"


def test_select_pairs_keeps_two_nearest_admissible_per_direction():
    from types import SimpleNamespace as S

    ps = [S(level=x) for x in (79_000, 79_700, 79_800, 79_850, 80_000, 80_160, 80_200, 80_300, 81_000)]
    a, up, down = 79_999.99, 182, 152
    plan, adm = le.select_pairs(a, ps, up, down)
    lv = {d: [p.level for p in xs] for d, xs in adm.items()}
    # every admissible strike is counted ...
    assert lv == {"k_yes_pm_no": [80_160, 80_200, 80_300, 81_000], "pm_yes_k_no": [79_000, 79_700, 79_800]}
    # ... but only the 2 nearest per direction are scored, plus the near-exact strike (80,000)
    assert [(p.level, kind, dirs) for p, kind, dirs in plan] == [
        (80_000, "near_exact", ["k_yes_pm_no", "pm_yes_k_no"]),
        (80_160, "arb", ["k_yes_pm_no"]), (80_200, "arb", ["k_yes_pm_no"]),
        (79_800, "arb", ["pm_yes_k_no"]), (79_700, "arb", ["pm_yes_k_no"])]


def test_aligned_count():
    from datetime import UTC, datetime, timedelta

    t0 = datetime(2026, 10, 20, tzinfo=UTC)
    k = [t0 + timedelta(seconds=s) for s in (0, 60, 120, 180)]
    pm = [t0 + timedelta(seconds=s) for s in (5, 95, 300)]
    assert le.aligned_count(k, pm) == 2  # 0 (5 s off) and 120 (25 s); 60 is 35 s from 95, 180 has nothing near
    assert le.aligned_count(k, [t0 + timedelta(seconds=s) for s in (-30, 70, 150)]) == 4  # 120 & 180 both near 150
    assert le.aligned_count(k, []) == 0
