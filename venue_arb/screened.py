"""Screened tier (pure): hourly price gaps from history. Polymarket history is a traded/mid price, not an ask, so
nothing here is executable and it must never be mixed with the executable edges."""
from bisect import bisect_left
from datetime import timedelta

from venue_arb.exact import ExactPair
from venue_arb.fees import kalshi_taker_fee, polymarket_taker_fee

MAX_SKEW = timedelta(minutes=30)  # a Polymarket print further than this from the Kalshi close instant: no row


def _nearest(pm: list[dict], times: list, ts) -> dict | None:
    """The print nearest `ts` within MAX_SKEW (ties go to the earlier print); `pm` sorted, `times` its timestamps."""
    i = bisect_left(times, ts)
    near = min(pm[max(i - 1, 0):i + 1], key=lambda r: abs(r["ts"] - ts), default=None)
    return near if near and abs(near["ts"] - ts) <= MAX_SKEW else None


def gaps(pair: ExactPair, k: list[dict], pm: list[dict], series_fee: dict) -> list[dict]:
    """One row per Kalshi candle close instant and direction, against the Polymarket print nearest that instant
    (within MAX_SKEW). A candle without a print that close, or with an empty book, gives no row."""
    pm = sorted(pm, key=lambda r: r["ts"])
    times = [r["ts"] for r in pm]
    fee_type, mult = series_fee["fee_type"], series_fee["fee_multiplier"]
    out = []
    for c in k:
        ts = c["ts"]
        near = _nearest(pm, times, ts)
        if near is None:
            continue
        pm_yes = near["price"]
        pm_legs = {"k_yes_pm_no": 1 - pm_yes, "k_no_pm_yes": pm_yes}  # price of the Polymarket token bought
        if pair.yes_maps_to == "no":  # Kalshi yes is Polymarket no: the Polymarket leg flips
            pm_legs = {"k_yes_pm_no": pm_yes, "k_no_pm_yes": 1 - pm_yes}
        k_legs = {"k_yes_pm_no": c["yes_ask"], "k_no_pm_yes": None if c["yes_bid"] is None else 1 - c["yes_bid"]}
        for d, kp in k_legs.items():
            if kp is None:
                continue
            pp = pm_legs[d]
            gross = 1 - kp - pp
            net = gross - kalshi_taker_fee(kp, 1, fee_type, mult) - polymarket_taker_fee(
                pp, 1, pair.pm_fee_schedule, pair.pm_fees_enabled)
            out.append({"ts": ts, "kalshi_id": pair.kalshi_id, "pm_id": pair.pm_id, "direction": d,
                        "k_price": kp, "pm_price": pp, "gross_gap": gross, "net_gap_top": net})
    return out
