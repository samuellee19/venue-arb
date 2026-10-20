"""Cross-venue ladder spreads at one same-instant snapshot (pure: no qa import).

Kalshi strike a pays Yes iff BRTI > a; Polymarket strike b pays Yes iff Binance > b (levels normalized by crypto_rule).
Basis diff = BRTI - Binance lies in [-down, up] (venue_arb.basis.bounds).

- k_yes_pm_no (Kalshi Yes > a + PM No > b) pays 0 only if BRTI <= a and Binance > b, which needs
  Binance - BRTI > b - a. Impossible within the basis range iff b - a >= down.
- pm_yes_k_no (PM Yes > b + Kalshi No > a) pays 0 only if Binance <= b and BRTI > a, which needs
  BRTI - Binance > a - b. Impossible within the basis range iff a - b >= up.
In every other state at least one leg pays $1.
"""
from bisect import bisect_left
from collections.abc import Callable
from datetime import datetime, timedelta
from typing import Literal

from venue_arb.edges import Edge, walk
from venue_arb.episodes import ALIGN_S, Episode, align, find
from venue_arb.fees import kalshi_taker_fee, polymarket_taker_fee
from venue_arb.models import Level

Direction = Literal["k_yes_pm_no", "pm_yes_k_no"]
K_NEAREST = 2  # admissible PM strikes scored per Kalshi strike and direction (Ruling 7)
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


def is_near_exact(a: float, b: float) -> bool:
    """Same strike (within $1 after normalization): scored like a spread, recorded with (up, down), never arbitrage."""
    return abs(_gap(a, b)) <= NEAR_EXACT_TOL


def classify(a: float, b: float, up: float, down: float) -> tuple[str, list[Direction]] | None:
    """("near_exact", both directions) first (Ruling 8), else ("arb", admissible directions), else None."""
    if is_near_exact(a, b):
        return "near_exact", ["k_yes_pm_no", "pm_yes_k_no"]
    if dirs := admissible(a, b, up, down):
        return "arb", dirs
    return None


def select_pairs(a: float, ps: list, up: float, down: float, k_nearest: int = K_NEAREST):
    """For Kalshi strike a and PM strikes ps (anything with .level): (plan, admissible). plan = [(p, kind, dirs)]: the
    near-exact strikes, then per direction the k_nearest admissible strikes nearest to a (k_yes_pm_no: smallest b with
    b - a >= down upward; pm_yes_k_no: largest b with a - b >= up downward). admissible = every admissible strike per
    direction, ascending by level, for the check count."""
    plan: list = []
    adm: dict[str, list] = {"k_yes_pm_no": [], "pm_yes_k_no": []}
    for p in sorted(ps, key=lambda p: p.level):
        if c := classify(a, p.level, up, down):
            if c[0] == "near_exact":
                plan.append((p, *c))
            else:
                for d in c[1]:
                    adm[d].append(p)
    plan += [(p, "arb", ["k_yes_pm_no"]) for p in adm["k_yes_pm_no"][:k_nearest]]
    plan += [(p, "arb", ["pm_yes_k_no"]) for p in adm["pm_yes_k_no"][::-1][:k_nearest]]
    return plan, adm


def aligned_count(k_ts: list[datetime], pm_ts: list[datetime], tol_s: int = ALIGN_S) -> int:
    """Kalshi times (sorted) with a Polymarket time (sorted) within tol_s: the snapshot count of one strike pair for the
    check denominator. Unlike episodes.align, a Polymarket time may serve two Kalshi times (rare at a 60 s cadence)."""
    tol = timedelta(seconds=tol_s)
    n = 0
    for t in k_ts:
        i = bisect_left(pm_ts, t - tol)
        n += i < len(pm_ts) and pm_ts[i] <= t + tol
    return n


def score_spread(direction: Direction, k_levels: list[Level], pm_levels: list[Level], series_fee: dict,
                 pm_schedule: dict | None, pm_fees_enabled: bool, min_edge: float = 0.01) -> Edge:
    if direction not in _LEGS:
        raise ValueError(f"unknown ladder direction {direction!r}")
    # fixed leg mapping: crypto_rule only admits Polymarket markets whose yes_label is "Yes", so PM Yes = above b
    k_side, pm_side = _LEGS[direction]

    def fee_k(p, q):
        return kalshi_taker_fee(p, q, series_fee["fee_type"], series_fee["fee_multiplier"])

    def fee_pm(p, q):
        return polymarket_taker_fee(p, q, pm_schedule, pm_fees_enabled)

    k_asks = [lv for lv in k_levels if lv.side == k_side]
    pm_asks = [lv for lv in pm_levels if lv.side == pm_side]
    return Edge(direction, *walk(k_asks, pm_asks, fee_k, fee_pm, min_edge))


def spread_points(direction: Direction, k: dict[datetime, list[Level]], pm: dict[datetime, list[Level]],
                  instant: datetime, series_fee: dict, pm_schedule: dict | None, pm_fees_enabled: bool
                  ) -> list[tuple[datetime, Edge]]:
    """Edges at aligned snapshots, stamped with the Kalshi time. Only snapshots strictly before the settlement instant:
    from the instant on, books are stale (the outcome is being fixed), so nothing there is executable."""
    kt_ok, pt_ok = [t for t in k if t < instant], [t for t in pm if t < instant]
    return [(kt, score_spread(direction, k[kt], pm[pt], series_fee, pm_schedule, pm_fees_enabled))
            for kt, pt in align(kt_ok, pt_ok)]


def score_ladder(asset: str, instant: datetime, ladder: dict, snaps: dict, up: float, down: float,
                 k_fee: Callable[[str], dict], pm_fee: Callable[[str], tuple[dict | None, bool]], fee_version: str
                 ) -> tuple[list[dict], list[Episode], dict[str, int]]:
    """Edge rows, arb episodes and counts for one same-instant ladder (crypto_rule.Strike lists per venue). `snaps` is
    scoring.snapshots output; strikes without snapshots are skipped. Per Kalshi strike only select_pairs' plan is
    scored; admissible_checks counts every admissible (a, b, direction) at every aligned snapshot (not stored). Only
    'arb' pairs form episodes: near-exact pairs carry basis exposure. k_fee(kalshi_id) -> series fee;
    pm_fee(pm_id) -> (feeSchedule, feesEnabled)."""
    ks = [s for s in ladder["kalshi"] if ("kalshi", s.market_id) in snaps]
    ps = [s for s in ladder["polymarket"] if ("polymarket", s.market_id) in snaps]
    pm_ts = {p.market_id: [t for t in snaps[("polymarket", p.market_id)] if t < instant] for p in ps}  # times sorted
    rows: list[dict] = []
    eps: list[Episode] = []
    pairs = checks = 0
    for k in ks:
        k_snaps = snaps[("kalshi", k.market_id)]
        plan, adm = select_pairs(k.level, ps, up, down)
        k_ts = [t for t in k_snaps if t < instant]
        checks += sum(aligned_count(k_ts, pm_ts[p.market_id]) for xs in adm.values() for p in xs)
        pairs += len(plan)
        for p, kind, dirs in plan:
            for d in dirs:
                pts = spread_points(d, k_snaps, snaps[("polymarket", p.market_id)], instant, k_fee(k.market_id),
                                    *pm_fee(p.market_id))
                rows += [{"ts": t, "asset": asset, "instant": instant, "k_id": k.market_id, "pm_id": p.market_id,
                          "direction": d, "kind": kind, "a": k.level, "b": p.level, "basis_up": up,
                          "basis_down": down, "best_edge": e.best_edge, "capacity_contracts": e.capacity_contracts,
                          "capacity_usd": e.capacity_usd, "avg_edge": e.avg_edge, "fee_version": fee_version}
                         for t, e in pts]
                if kind == "arb":
                    eps += find(pts, instant, kalshi_id=k.market_id, pm_id=p.market_id, direction=d)
    return rows, eps, {"kalshi_strikes_booked": len(ks), "pm_strikes_booked": len(ps), "pairs_scored": pairs,
                       "admissible_checks": checks}
