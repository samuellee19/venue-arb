import pytest

from venue_arb import fees


def test_kalshi_fee_rounds_up_per_order():
    assert fees.kalshi_taker_fee(0.5, 1, "quadratic", 1) == 0.02
    assert fees.kalshi_taker_fee(0.5, 100, "quadratic", 1) == 1.75
    assert fees.kalshi_taker_fee(0.5, 100, "quadratic_with_maker_fees", 1) == 1.75


def test_kalshi_unknown_fee_type_raises():
    with pytest.raises(ValueError, match="flat"):
        fees.kalshi_taker_fee(0.5, 1, "flat", 1)


def test_polymarket_fee():
    s = {"rate": 0.05, "exponent": 1}
    assert fees.polymarket_taker_fee(0.5, 100, s, True) == pytest.approx(1.25)
    assert fees.polymarket_taker_fee(0.5, 100, s, False) == 0.0
    assert fees.polymarket_taker_fee(0.5, 100, None, True) == 0.0


def test_transfer_cost():
    assert fees.transfer_cost(1000) == 11.0
