"""Cross-venue ladder spreads at one same-instant snapshot (pure: no qa import).

Kalshi strike a pays Yes iff BRTI > a; Polymarket strike b pays Yes iff Binance > b (levels normalized by crypto_rule).
Basis diff = BRTI - Binance lies in [-down, up] (venue_arb.basis.bounds).

- k_yes_pm_no (Kalshi Yes > a + PM No > b) pays 0 only if BRTI <= a and Binance > b, which needs
  Binance - BRTI > b - a. Impossible within the basis range iff b - a >= down.
- pm_yes_k_no (PM Yes > b + Kalshi No > a) pays 0 only if Binance <= b and BRTI > a, which needs
  BRTI - Binance > a - b. Impossible within the basis range iff a - b >= up.
In every other state at least one leg pays $1.
"""
from typing import Literal

from venue_arb.edges import Edge, walk
from venue_arb.fees import kalshi_taker_fee, polymarket_taker_fee
from venue_arb.models import Level

Direction = Literal["k_yes_pm_no", "pm_yes_k_no"]
NEAR_EXACT_TOL = 1.0  # $: "same strike" after normalization (Kalshi T81499.99 vs PM above 81500)
_LEGS = {"k_yes_pm_no": ("yes", "no"), "pm_yes_k_no": ("no", "yes")}  # direction -> (Kalshi side, PM side)


def _gap(x: float, y: float) -> float:
    return round(x - y, 6)  # strikes are cents; drop float noise like 152.00000000000728


def admissible(a: float, b: float, up: float, down: float) -> list[Direction]:
    out: list[Direction] = []
    if _gap(b, a) >= down:
        out.append("k_yes_pm_no")
    if _gap(a, b) >= up:
        out.append("pm_yes_k_no")
    return out


def is_near_exact(a: float, b: float, up: float, down: float) -> bool:
    """Same strike in spirit but not basis-proof: scored like a spread, recorded with (up, down), never arbitrage."""
    return not admissible(a, b, up, down) and abs(_gap(a, b)) <= NEAR_EXACT_TOL


def score_spread(direction: Direction, k_levels: list[Level], pm_levels: list[Level], series_fee: dict,
                 pm_schedule: dict | None, pm_fees_enabled: bool, min_edge: float = 0.01) -> Edge:
    if direction not in _LEGS:
        raise ValueError(f"unknown ladder direction {direction!r}")
    k_side, pm_side = _LEGS[direction]

    def fee_k(p, q):
        return kalshi_taker_fee(p, q, series_fee["fee_type"], series_fee["fee_multiplier"])

    def fee_pm(p, q):
        return polymarket_taker_fee(p, q, pm_schedule, pm_fees_enabled)

    k_asks = [lv for lv in k_levels if lv.side == k_side]
    pm_asks = [lv for lv in pm_levels if lv.side == pm_side]
    return Edge(direction, *walk(k_asks, pm_asks, fee_k, fee_pm, min_edge))
