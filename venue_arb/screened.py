"""Screened tier (pure): hourly price gaps from history. Polymarket history is a traded/mid price, not an ask, so
nothing here is executable and it must never be mixed with the executable edges."""
from venue_arb.exact import ExactPair
from venue_arb.fees import kalshi_taker_fee, polymarket_taker_fee


def _hour(ts):
    return ts.replace(minute=0, second=0, microsecond=0)


def gaps(pair: ExactPair, k: list[dict], pm: list[dict], series_fee: dict) -> list[dict]:
    """One row per hour present on both sides and direction. An hour missing on either side gives no row."""
    pms = {_hour(r["ts"]): r["price"] for r in pm}  # last print in the hour wins
    fee_type, mult = series_fee["fee_type"], series_fee["fee_multiplier"]
    out = []
    for c in k:
        ts = _hour(c["ts"])
        if ts not in pms:
            continue
        pm_yes = pms[ts]
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
