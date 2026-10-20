"""Parse crypto "price above X at instant T" listings and group them into same-instant ladders (pure; no qa, no LLM).

Verified 2026-10-20 against live listings (venue_arb.listings) and their rule text:
- Kalshi KXBTCD/KXETHD (and the "or above" tails of KXBTC/KXETH): yes_label "$X or above", ticker T{X-0.01}; rules: "If the
  simple average of the sixty seconds of CF Benchmarks' Bitcoin Real-Time Index (BRTI) before 4 AM EDT is above 81499.99
  at 4 AM EDT on Oct 20, 2026, then the market resolves to Yes" for "$81,500 or above". So the test is STRICT: index >
  X-0.01, normalized to level = X-0.01. close_time is the settlement instant. yes_token is None (read from the ticker).
- Polymarket hourly "Bitcoin above X on <Month D>, <h>AM ET?": "Close price for the BTC/USDT 1 hour candle that ends on
  the time and date specified in the title is higher than the price specified in the title" -> strict, level = X, instant =
  the hour in the title (ET); close_time agrees.
- Polymarket daily "Will the price of Bitcoin be above $X on <Month D>?": Binance 1-minute candle closing at 12:00 ET,
  "Close price higher than" -> strict, level = X.
- Polymarket daily "greater than $X" is the top bracket of a range ladder: "If the reported value falls exactly between
  two brackets ... resolve to the higher range bracket", so X itself is Yes (>= X). Prices have 2 decimals, so it is
  normalized to level = X-0.01 like Kalshi.
Not parsed: ranges, "below / less than / or below", Up/Down, reach/dip, and other shapes.
"""

import re
from calendar import month_name
from datetime import UTC, date, datetime
from typing import Literal, NamedTuple
from zoneinfo import ZoneInfo

ET = ZoneInfo("America/New_York")
_ASSET = {"KXBTC": "BTC", "KXBTCD": "BTC", "KXETH": "ETH", "KXETHD": "ETH", "Bitcoin": "BTC", "Ethereum": "ETH"}
_N = r"\$?([\d,]+(?:\.\d+)?)"
_MONTH = {m.lower(): i for i, m in enumerate(month_name) if m}
_MD = r"(?P<mon>[A-Z][a-z]+) (?P<day>\d+)"
_K_ABOVE = re.compile(rf"^{_N} or above$")
_K_BELOW = re.compile(rf"^{_N} or below$")
_K_RANGE = re.compile(rf"^{_N} to {_N}$")
_P_DAILY = re.compile(rf"^Will the price of (Bitcoin|Ethereum) be (above|greater than|less than) {_N} on {_MD}\?$")
_P_RANGE = re.compile(rf"^Will the price of (Bitcoin|Ethereum) be between {_N} and {_N} on \w+ \d+\?$")
_P_HOURLY = re.compile(rf"^(Bitcoin|Ethereum) above {_N} on {_MD}, (?P<h>\d+)(?P<ap>AM|PM) ET\?$")

Parsed = tuple[str, date, str, float, float | None]  # asset, ET date, kind (above/below/range), lo, hi


def _f(s: str) -> float:
    return float(s.replace(",", ""))


def parse(row: dict) -> Parsed | None:
    """Coarse shape for the bake-off truth set: asset, ET date of close_time, kind, bounds as printed."""
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


class Strike(NamedTuple):
    venue: str
    market_id: str
    yes_token: str | None
    asset: Literal["BTC", "ETH"]
    instant: datetime
    op: Literal["gt"]
    level: float  # Yes iff settlement value > level


def _et(row: dict, m: re.Match, hour: int) -> datetime | None:
    """Title month/day at `hour` ET as UTC; the year comes from close_time. None for an impossible date or hour, or an
    ambiguous (DST fall-back) or nonexistent (spring-forward) wall time."""
    month = _MONTH.get(m["mon"].lower())
    if month is None or not 0 <= hour < 24:
        return None
    try:
        at = datetime(row["close_time"].astimezone(ET).year, month, int(m["day"]), hour, tzinfo=ET)
    except ValueError:
        return None
    # fall-back hour is ambiguous (two UTC instants); spring-forward hour does not exist (round trip shifts the wall time)
    if at.replace(fold=1).utcoffset() != at.utcoffset() or at.astimezone(UTC).astimezone(ET).replace(tzinfo=None) != at.replace(tzinfo=None):
        return None
    return at.astimezone(UTC)


def parse_strike(row: dict) -> Strike | None:
    if row["venue"] == "kalshi":
        asset = _ASSET.get((row["event_id"] or "").split("-")[0])
        m = _K_ABOVE.match(row["yes_label"] or "")
        if not (asset and m):
            return None
        return Strike("kalshi", row["market_id"], None, asset, row["close_time"], "gt", round(_f(m[1]) - 0.01, 2))
    if row["venue"] != "polymarket" or row["yes_label"] != "Yes":
        return None
    title = row["title"]
    if m := _P_HOURLY.match(title):
        if not 1 <= int(m["h"]) <= 12:
            return None
        at = _et(row, m, int(m["h"]) % 12 + (12 if m["ap"] == "PM" else 0))
        return at and Strike("polymarket", row["market_id"], row["yes_token"], _ASSET[m[1]], at, "gt", _f(m[2]))
    if (m := _P_DAILY.match(title)) and m[2] != "less than":
        level = _f(m[3]) if m[2] == "above" else round(_f(m[3]) - 0.01, 2)
        at = _et(row, m, 12)
        return at and Strike("polymarket", row["market_id"], row["yes_token"], _ASSET[m[1]], at, "gt", level)
    return None


Ladder = dict[str, list[Strike]]


def ladders(rows: list[dict]) -> dict[tuple[str, datetime], Ladder]:
    """Strikes grouped by (asset, settlement instant); only groups with both venues, each sorted by level."""
    out: dict[tuple[str, datetime], Ladder] = {}
    for r in rows:
        if r["category"] == "crypto_price" and (s := parse_strike(r)):
            out.setdefault((s.asset, s.instant), {}).setdefault(s.venue, []).append(s)
    return {k: {v: sorted(xs, key=lambda s: s.level) for v, xs in lad.items()}
            for k, lad in out.items() if {"kalshi", "polymarket"} <= lad.keys()}


def clip(ladder: Ladder, pad: float) -> Ladder:
    """Keep Kalshi strikes within [pm_min - pad, pm_max + pad]; Polymarket untouched."""
    levels = [s.level for s in ladder["polymarket"]]
    lo, hi = min(levels) - pad, max(levels) + pad
    return {**ladder, "kalshi": [s for s in ladder["kalshi"] if lo <= s.level <= hi]}
