"""Private check that the venue oracle is a fair stand-in for the Schwab quote. Schwab quotes are licensed for
personal use only, so this feeds a git-ignored report and warn-level qa results, never the public publisher."""

from datetime import date, datetime, time, timedelta
from statistics import median
from zoneinfo import ZoneInfo

from venue_arb import sessions
from venue_arb.config import REF_MAX_AGE_S
from venue_arb.sources.hyperliquid import Label

ET = ZoneInfo("America/New_York")
DRIFT_P95_BPS = 10
DAYS = 5  # ET days each run covers
TABLE = "venue_arb.stock_perp_oracle_check"
REGULAR = (time(9, 30), time(16, 0))  # the US regular session; the rest of the external day is "extended"
SEGMENTS = ("regular", "extended")


def segment(local: datetime) -> str:
    """'regular' inside [09:30, 16:00) ET, else 'extended' (pre-market, post-market). Extended-hours NBBOs on thin
    names are wide, so the two segments are reported apart and only the regular one drives the drift warn."""
    return "regular" if REGULAR[0] <= local.time() < REGULAR[1] else "extended"


def fresh(ts: datetime, quote_time: datetime, max_age_s: int = REF_MAX_AGE_S) -> bool:
    """A live Schwab ref counts only while its quote is at most max_age_s older than the tick that stored it (the
    executable tier's rule): on a US holiday Monday the collector stores Friday's quote under a fresh ts."""
    return ts - quote_time <= timedelta(seconds=max_age_s)


def _p95(xs: list[float]) -> float:
    return sorted(xs)[-(-95 * len(xs) // 100) - 1]  # nearest rank


def compare(ctx_rows: list[dict], ref_rows: list[dict], labels: dict[str, Label]) -> list[dict]:
    """Rows (day, symbol, segment, n, median_abs_bps, p95_abs_bps, share_over_10, open_gap_bps) per (symbol, ET day,
    segment).
    ctx_rows: symbol, ts, oracle. ref_rows: symbol, ts, bid, ask, quote_time (live 'schwab' only). Only ticks with a
    usable quote (fresh, 0 < bid <= ask) on a US-listed name in an external session count; the join key is the shared
    tick ts. open_gap_bps (Mondays, signed): oracle over mid at the first usable tick in [09:30, 09:35) ET."""
    oracle = {(r["symbol"], r["ts"]): r["oracle"] for r in ctx_rows}
    gaps: dict[tuple, list[float]] = {}
    opens: dict[tuple, list] = {}  # (symbol, day) -> [oracle, mid] at the first usable tick in [09:30, 09:35)
    for r in sorted(ref_rows, key=lambda r: r["ts"]):
        sym, ts = r["symbol"], r["ts"]
        label = labels.get(sym)
        orc = oracle.get((sym, ts))
        if (label is None or label.kind == "pre_ipo" or label.exchange.strip().upper() not in sessions._US or orc is None
                or not sessions.external(label, ts) or not fresh(ts, r["quote_time"]) or not 0 < r["bid"] <= r["ask"]):
            continue
        mid = (r["bid"] + r["ask"]) / 2
        local = ts.astimezone(ET)
        key = (sym, local.date(), segment(local))
        gaps.setdefault(key, []).append(abs(orc / mid - 1) * 1e4)
        if local.weekday() == 0 and time(9, 30) <= local.time() < time(9, 35) and key not in opens:
            opens[key] = [orc, mid]  # first usable tick within 5 minutes of the open
    out = []
    for (sym, day, seg), xs in sorted(gaps.items()):
        o = opens.get((sym, day, "regular"))  # the open is a regular-session tick
        gap = (o[0] / o[1] - 1) * 1e4 if o and seg == "regular" else None
        out.append({"day": day, "symbol": sym, "segment": seg, "n": len(xs), "median_abs_bps": median(xs),
                    "p95_abs_bps": _p95(xs), "share_over_10": sum(x > DRIFT_P95_BPS for x in xs) / len(xs),
                    "open_gap_bps": gap})
    return out


def drift_results(checks, rows: list[dict], days: int = 5, end_day: date | None = None) -> list:
    """Warn `oracle_drift` per symbol whose worst daily regular-session p95 over the `days` ET days ending at `end_day`
    (default: the newest row's day) exceeds 10 bps; extended-hours rows are reported, never warned on. `checks` is
    the qa.checks module (kept out of this file's imports). ponytail: the worst day stands in for a pooled p95."""
    end_day = end_day or max((r["day"] for r in rows), default=None)
    worst: dict[str, float] = {}
    for r in rows:
        if r["segment"] == "regular" and r["day"] > end_day - timedelta(days=days):
            worst[r["symbol"]] = max(worst.get(r["symbol"], 0.0), r["p95_abs_bps"])
    return [checks.Result("oracle_drift", False, 1, detail=f"{s} trailing p95 {v:.1f} bps > {DRIFT_P95_BPS}",
                          severity="warn", target=TABLE) for s, v in sorted(worst.items()) if v > DRIFT_P95_BPS]


def report(rows: list[dict], day: date) -> str:
    """The private Markdown report: one table per segment, regular session first, worst p95 first within each."""
    lines = [f"# Oracle vs Schwab, {day}", "",
             (f"Last {DAYS} ET days. Private. Coverage: US-listed names only, external-session ticks with a fresh "
              "two-sided Schwab quote at the same tick, so in practice about 04:00-20:00 ET; the overnight external "
              "hours (20:00-04:00 ET) are not validated. The newest day is partial and is rewritten by the next run."),
             ""]
    titles = {"regular": (f"## Regular session ({REGULAR[0]:%H:%M}-{REGULAR[1]:%H:%M} ET)",
                          (f"The `oracle_drift` warn reads this table (regular session only): a name whose trailing "
                           f"{DAYS}-day p95 exceeds {DRIFT_P95_BPS} bps.")),
              "extended": ("## Extended hours (pre-market and post-market)",
                           ("For information: extended-hours NBBOs on thin names are wide, so a gap here does not say "
                            "which side is off."))}
    for seg in SEGMENTS:
        title, note = titles[seg]
        lines += [title, "", note, "",
                  "| day | symbol | n | median bps | p95 bps | share >10 bps | Monday open gap bps |",
                  "|---|---|---|---|---|---|---|"]
        for r in sorted((r for r in rows if r["segment"] == seg), key=lambda r: -r["p95_abs_bps"]):
            gap = "" if r["open_gap_bps"] is None else f"{r['open_gap_bps']:.1f}"
            lines.append(f"| {r['day']} | {r['symbol']} | {r['n']} | {r['median_abs_bps']:.1f} | "
                         f"{r['p95_abs_bps']:.1f} | {r['share_over_10']:.2f} | {gap} |")
        lines.append("")
    return "\n".join(lines)
