"""Private check that the venue oracle is a fair stand-in for the Schwab quote. Schwab quotes are licensed for
personal use only, so this feeds a git-ignored report and warn-level qa results, never the public publisher."""

import math
from datetime import datetime, time, timedelta
from statistics import median
from zoneinfo import ZoneInfo

from venue_arb import sessions
from venue_arb.config import REF_MAX_AGE_S
from venue_arb.sources.hyperliquid import Label

ET = ZoneInfo("America/New_York")
DRIFT_P95_BPS = 10
TABLE = "venue_arb.stock_perp_oracle_check"


def fresh(ts: datetime, quote_time: datetime, max_age_s: int = REF_MAX_AGE_S) -> bool:
    """A live Schwab ref counts only while its quote is at most max_age_s older than the tick that stored it (the
    executable tier's rule): on a US holiday Monday the collector stores Friday's quote under a fresh ts."""
    return ts - quote_time <= timedelta(seconds=max_age_s)


def _p95(xs: list[float]) -> float:
    return sorted(xs)[math.ceil(0.95 * len(xs)) - 1]  # nearest rank


def compare(ctx_rows: list[dict], ref_rows: list[dict], labels: dict[str, Label]) -> list[dict]:
    """Rows (day, symbol, n, median_abs_bps, p95_abs_bps, share_over_10, open_gap_bps) per (symbol, ET day).
    ctx_rows: symbol, ts, oracle. ref_rows: symbol, ts, bid, ask, quote_time (live 'schwab' only). Only ticks with a
    usable quote (fresh, 0 < bid <= ask) on a US-listed name in an external session count; the join key is the shared
    tick ts. open_gap_bps (Mondays, signed): the 09:30 oracle print over the first usable Schwab mid at or after 09:30."""
    oracle = {(r["symbol"], r["ts"]): r["oracle"] for r in ctx_rows}
    gaps: dict[tuple, list[float]] = {}
    opens: dict[tuple, list] = {}  # (symbol, day) -> [oracle at 09:30 or None, first mid at/after 09:30 or None]
    for r in sorted(ref_rows, key=lambda r: r["ts"]):
        sym, ts = r["symbol"], r["ts"]
        label = labels.get(sym)
        orc = oracle.get((sym, ts))
        if (label is None or label.exchange.strip().upper() not in sessions._US or orc is None
                or not sessions.external(label, ts) or not fresh(ts, r["quote_time"]) or not 0 < r["bid"] <= r["ask"]):
            continue
        mid = (r["bid"] + r["ask"]) / 2
        local = ts.astimezone(ET)
        key = (sym, local.date())
        gaps.setdefault(key, []).append(abs(orc / mid - 1) * 1e4)
        if local.weekday() == 0 and local.time() >= time(9, 30):
            o = opens.setdefault(key, [None, None])
            if o[1] is None:
                o[1] = mid
                if local.time() < time(9, 31):
                    o[0] = orc
    out = []
    for (sym, day), xs in sorted(gaps.items()):
        o = opens.get((sym, day))
        gap = (o[0] / o[1] - 1) * 1e4 if o and o[0] is not None else None
        out.append({"day": day, "symbol": sym, "n": len(xs), "median_abs_bps": median(xs), "p95_abs_bps": _p95(xs),
                    "share_over_10": sum(x > DRIFT_P95_BPS for x in xs) / len(xs), "open_gap_bps": gap})
    return out


def drift_results(checks, rows_5d: list[dict]) -> list:
    """Warn `oracle_drift` per symbol whose worst daily p95 over the given trailing days exceeds 10 bps. `checks` is
    the qa.checks module (kept out of this file's imports). ponytail: the worst day stands in for a pooled p95."""
    worst: dict[str, float] = {}
    for r in rows_5d:
        worst[r["symbol"]] = max(worst.get(r["symbol"], 0.0), r["p95_abs_bps"])
    return [checks.Result("oracle_drift", False, 1, detail=f"{s} trailing p95 {v:.1f} bps > {DRIFT_P95_BPS}",
                          severity="warn", target=TABLE) for s, v in sorted(worst.items()) if v > DRIFT_P95_BPS]
