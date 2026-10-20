"""Settlement-source basis: Kalshi BRTI settle vs the Binance candle Polymarket names (pure; no qa)."""

import math
from datetime import UTC, datetime, timedelta

_ASSET = {"KXBTCD": "BTC", "KXETHD": "ETH"}


def brti_by_instant(markets: list[dict]) -> dict[tuple[str, datetime], float]:
    """One BRTI settle per (asset, on-the-hour instant): every strike of an event carries the same expiration_value."""
    out: dict[tuple[str, datetime], float] = {}
    for m in markets:
        asset = _ASSET.get(m["event_ticker"].split("-")[0])
        t = datetime.fromisoformat(m["close_time"]).astimezone(UTC)
        if not asset or not m.get("expiration_value") or t.minute or t.second:
            continue
        v = float(m["expiration_value"])
        if out.setdefault((asset, t), v) != v:
            raise ValueError(f"conflicting BRTI settle for {asset} {t}")
    return out


def by_end(asset: str, candles: list[dict], seconds: int) -> dict[tuple[str, datetime], float]:
    """Candle closes keyed by the instant the candle ends (the instant Polymarket's rules name)."""
    return {(asset, c["open"] + timedelta(seconds=seconds)): c["close"] for c in candles}


def join(brti: dict, h1: dict) -> list[dict]:
    """Rows for instants with BRTI and the 1h candle ending there (`binance`, `diff`). The 1m columns are no longer
    populated (NaN): the 1m candle closing at H has the same close as the 1h candle ending at H."""
    nan = math.nan
    return [{"instant": t, "asset": a, "brti": b, "binance": h1[(a, t)], "diff": b - h1[(a, t)],
             "binance_1m": nan, "diff_1m": nan} for (a, t), b in sorted(brti.items()) if (a, t) in h1]


def _rank_up(xs: list[float], q: float) -> float:
    """Nearest-rank q-quantile of xs, floored at 0, rounded up to $1."""
    if not xs:
        raise ValueError("no basis observations")
    s = sorted(xs)
    return float(max(0, math.ceil(s[math.ceil(round(q * len(s), 9)) - 1] - 1e-9)))


def bounds(diffs: list[float], q: float = 0.999) -> tuple[float, float]:
    """Directional basis bounds (up, down) for diff = BRTI - Binance: BRTI exceeds Binance by at most `up`, falls short
    by at most `down`, each at quantile q, rounded up to $1."""
    return _rank_up(diffs, q), _rank_up([-d for d in diffs], q)
