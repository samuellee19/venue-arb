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


def join(brti: dict, h1: dict, m1: dict) -> list[dict]:
    """Rows for instants with BRTI and the 1h candle (the one Polymarket's hourly rules name -> `binance`, `diff`);
    the 1m candle (the daily noon-ET rule) is carried alongside, NaN when its day is missing."""
    out = []
    for (asset, t), b in sorted(brti.items()):
        if (asset, t) not in h1:
            continue
        x = m1.get((asset, t), math.nan)
        out.append({"instant": t, "asset": asset, "brti": b, "binance": h1[(asset, t)], "diff": b - h1[(asset, t)],
                    "binance_1m": x, "diff_1m": b - x})
    return out


def bound(diffs: list[float], q: float = 0.999) -> float:
    """Basis bound B: nearest-rank q-quantile of |diff|, rounded up to $1."""
    if not diffs:
        raise ValueError("no basis observations")
    s = sorted(abs(d) for d in diffs)
    return float(math.ceil(s[math.ceil(q * len(s)) - 1] - 1e-9))
