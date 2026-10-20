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

import re
from datetime import date
from zoneinfo import ZoneInfo

ET = ZoneInfo("America/New_York")
_ASSET = {"KXBTC": "BTC", "KXBTCD": "BTC", "KXETH": "ETH", "KXETHD": "ETH", "Bitcoin": "BTC", "Ethereum": "ETH"}
_N = r"\$?([\d,]+(?:\.\d+)?)"
_K_ABOVE = re.compile(rf"^{_N} or above$")
_K_BELOW = re.compile(rf"^{_N} or below$")
_K_RANGE = re.compile(rf"^{_N} to {_N}$")
_P_DAILY = re.compile(rf"^Will the price of (Bitcoin|Ethereum) be (above|greater than|less than) {_N} on \w+ \d+\?$")
_P_RANGE = re.compile(rf"^Will the price of (Bitcoin|Ethereum) be between {_N} and {_N} on \w+ \d+\?$")
_P_HOURLY = re.compile(rf"^(Bitcoin|Ethereum) above {_N} on \w+ \d+, \d+(?:AM|PM) ET\?$")

Parsed = tuple[str, date, str, float, float | None]  # asset, ET date, kind (above/below/range), lo, hi


def _f(s: str) -> float:
    return float(s.replace(",", ""))


def parse(row: dict) -> Parsed | None:
    day = row["close_time"].astimezone(ET).date()
    if row["venue"] == "kalshi":
        asset = _ASSET.get((row["event_id"] or "").split("-")[0])
        label = row["yes_label"] or ""
        if not asset:
            return None
        if m := _K_ABOVE.match(label):
            return asset, day, "above", _f(m[1]), None
        if m := _K_BELOW.match(label):
            return asset, day, "below", _f(m[1]), None
        if m := _K_RANGE.match(label):
            return asset, day, "range", _f(m[1]), round(_f(m[2]) + 0.01, 2)
        return None
    if row["yes_label"] != "Yes":
        return None
    title = row["title"]
    if m := _P_DAILY.match(title):
        return _ASSET[m[1]], day, "below" if m[2] == "less than" else "above", _f(m[3]), None
    if m := _P_RANGE.match(title):
        return _ASSET[m[1]], day, "range", _f(m[2]), _f(m[3])
    if m := _P_HOURLY.match(title):
        return _ASSET[m[1]], day, "above", _f(m[2]), None
    return None


def _parsed(rows: list[dict]) -> list[tuple[dict, Parsed]]:
    return [(r, x) for r in rows if r["category"] == "crypto_price" and (x := parse(r))]


def crypto_pairs(kalshi: list[dict], pm: list[dict]) -> list[tuple[str, str, str]]:
    """Same asset, ET date, direction and threshold (range: both bounds)."""
    index: dict[Parsed, list[str]] = {}
    for r, x in _parsed(pm):
        index.setdefault(x, []).append(r["market_id"])
    return [(r["market_id"], b, f"crypto_{x[2]}") for r, x in _parsed(kalshi) for b in index.get(x, [])]

