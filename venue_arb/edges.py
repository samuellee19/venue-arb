"""Executable edge between a Kalshi market and its exact Polymarket counterpart (pure)."""
from collections.abc import Callable
from dataclasses import dataclass
from typing import Literal

from venue_arb.exact import ExactPair
from venue_arb.fees import kalshi_taker_fee, polymarket_taker_fee
from venue_arb.models import Level

Direction = Literal["k_yes_pm_no", "k_no_pm_yes", "pm_yes_k_no"]  # pm_yes_k_no: ladder spreads only
Fee = Callable[[float, float], float]  # (price, contracts) -> dollars


@dataclass(frozen=True)
class Edge:
    direction: Direction
    best_edge: float | None
    capacity_contracts: float
    capacity_usd: float
    avg_edge: float | None


def _slices(k_asks: list[Level], pm_asks: list[Level], limit: float | None = None):
    """Yield (k_price, pm_price, q) slices walking two best-first ladders; q is capped so the total is <= limit."""
    i = j = 0
    rk = k_asks[0].size if k_asks else 0
    rp = pm_asks[0].size if pm_asks else 0
    left = limit
    while i < len(k_asks) and j < len(pm_asks) and (left is None or left > 0):
        q = min(rk, rp) if left is None else min(rk, rp, left)
        if q > 0:
            yield k_asks[i].price, pm_asks[j].price, q
            if left is not None:
                left -= q
        rk -= q
        rp -= q
        if rk <= 0:
            i += 1
            rk = k_asks[i].size if i < len(k_asks) else 0
        if rp <= 0:
            j += 1
            rp = pm_asks[j].size if j < len(pm_asks) else 0


def walk(k_asks: list[Level], pm_asks: list[Level], fee_k: Fee, fee_pm: Fee, min_edge: float = 0.01):
    """Returns (best_edge, capacity_contracts, capacity_usd, avg_edge). Ladders are best-first.
    capacity_usd is money spent on both legs, excluding fees."""
    best = None
    cap = usd = total = 0.0
    for pk, pp, q in _slices(k_asks, pm_asks):
        # fees per slice with the slice's own count: Kalshi's cent rounding per slice is conservative
        edge = 1 - pk - pp - (fee_k(pk, q) + fee_pm(pp, q)) / q
        if best is None:
            best = edge
        if edge < min_edge:
            break
        cap += q
        usd += q * (pk + pp)
        total += q * edge
    return best, cap, usd, (total / cap if cap else None)


@dataclass(frozen=True)
class Fill:
    requested: float
    filled: float  # contracts both ladders could supply, <= requested
    insufficient_depth: bool
    k_avg_price: float | None
    pm_avg_price: float | None
    fees_usd: float
    cost_usd: float  # filled * (k_avg + pm_avg) + fees_usd
    net_cents: float | None
    net_bps: float | None


def fill(k_asks: list[Level], pm_asks: list[Level], fee_k: Fee, fee_pm: Fee, contracts: float) -> Fill:
    """Walk both best-first ladders to `contracts`, through negative slices; fees per slice as in `walk`."""
    if contracts <= 0:
        raise ValueError("contracts must be positive")
    filled = k_usd = pm_usd = fees = 0.0
    for pk, pp, q in _slices(k_asks, pm_asks, contracts):
        filled += q
        k_usd += q * pk
        pm_usd += q * pp
        fees += fee_k(pk, q) + fee_pm(pp, q)
    cost = k_usd + pm_usd + fees
    return Fill(contracts, filled, filled < contracts,
                k_usd / filled if filled else None, pm_usd / filled if filled else None, fees, cost,
                100 * (filled - cost) / filled if filled else None,
                1e4 * (filled - cost) / cost if filled else None)


def score(pair: ExactPair, k_levels: list[Level], pm_levels: list[Level], series_fee: dict,
          min_edge: float = 0.01) -> list[Edge]:
    def fee_k(p, q):
        return kalshi_taker_fee(p, q, series_fee["fee_type"], series_fee["fee_multiplier"])

    def fee_pm(p, q):
        return polymarket_taker_fee(p, q, pair.pm_fee_schedule, pair.pm_fees_enabled)

    def asks(levels, side):
        return [lv for lv in levels if lv.side == side]

    flip = {"yes": "no", "no": "yes"}
    out = []
    for direction, k_side in (("k_yes_pm_no", "yes"), ("k_no_pm_yes", "no")):
        # PM leg pays when the bought Kalshi side loses
        pm_side = flip[pair.yes_maps_to] if k_side == "yes" else pair.yes_maps_to
        out.append(Edge(direction, *walk(asks(k_levels, k_side), asks(pm_levels, pm_side), fee_k, fee_pm, min_edge)))
    return out
