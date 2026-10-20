"""Draft truth set for the embedding bake-off: BTC/ETH price near-pairs found by parsing titles.

Patterns seen in `venue_arb.listings` (current rows, 2026-10-19):

Kalshi (series in taxonomy.KALSHI_SERIES; CF Benchmarks RTI, 60 s average before the hour; closes 2am/3am/5pm EDT):
    KXBTCD / KXETHD  title "Bitcoin price on Oct 20, 2026?" / "Ethereum price at Oct 20, 2026 at 2am EDT?",
                     yes_label "$90,000 or above"   (ticker T89999.99: above 89,999.99, i.e. >= 90,000)
    KXBTC / KXETH    yes_label "$87,750 to 87,999.99" (range [87,750, 88,000)), tails "$X or below" / "$X or above"
Polymarket (Binance BTC/USDT or ETH/USDT 1-minute or 1-hour candle close, yes_label "Yes"):
    "Will the price of Bitcoin be above $80,000 on October 20?"              daily, noon ET
    "Will the price of Bitcoin be greater than $X ..." / "... less than $X ..."  tails of the daily range ladder
    "Will the price of Bitcoin be between $82,000 and $84,000 on October 20?"  daily range, noon ET
    "Bitcoin above 84,200 on October 20, 2AM ET?"                              hourly candle ending at that hour
Not parsed (different question shapes): "Up or Down", "reach / hit / dip to", weekly "green days", volatility index,
dominance, ETF flows, best-performing coin.

A pair is a near-match when asset, ET calendar date of close_time, direction and threshold (or both range bounds)
agree. Settlement source and time of day may differ; that is what makes them near-matches, not exact ones.
"""

from venue_arb.matching.crypto_rule import Parsed, parse


def _parsed(rows: list[dict]) -> list[tuple[dict, Parsed]]:
    return [(r, x) for r in rows if r["category"] == "crypto_price" and (x := parse(r))]


def crypto_pairs(kalshi: list[dict], pm: list[dict]) -> list[tuple[str, str, str]]:
    """Same asset, ET date, direction and threshold (range: both bounds)."""
    index: dict[Parsed, list[str]] = {}
    for r, x in _parsed(pm):
        index.setdefault(x, []).append(r["market_id"])
    return [(r["market_id"], b, f"crypto_{x[2]}") for r, x in _parsed(kalshi) for b in index.get(x, [])]

