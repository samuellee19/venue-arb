"""The weekend tier's pure logic: from Friday 8 PM to Sunday 8:05 PM New York the stock does not trade and the venue's
oracle floats on the perp's own price, so the question is how much of Monday's move the weekend perp already held.
Samples the perp at three points and the stock at Monday's pre-market and regular open; no I/O here."""

from datetime import date, datetime, time, timedelta
from statistics import median
from zoneinfo import ZoneInfo

from venue_arb.config import REF_MAX_AGE_S
from venue_arb.sessions import _US  # the US-listed exchange set, not duplicated
from venue_arb.sources.hyperliquid import Label

ET = ZoneInfo("America/New_York")
KST = ZoneInfo("Asia/Seoul")
SAMPLES = {"fri": ("Fri", time(20, 0)), "sat": ("Sat", time(12, 0)), "sun": ("Sun", time(18, 0))}
OFFSET = {"fri": 0, "sat": 1, "sun": 2}  # days after Friday
OPEN_PRE = time(20, 5)  # Sunday, ET: the overnight external restart
OPEN_REG = time(9, 30)
CLOSE_REG = time(16, 0)
OPEN_KRX = time(9, 0)  # Monday, Seoul
CLOSE_KRX = time(15, 30)
FLOOR_BPS = 10  # |open - friday| / friday below this is noise for the contained-share ratio
ENOUGH = 20  # the sign test's minimum sample


def at(day: date, t: time, tz: ZoneInfo = ET) -> datetime:
    return datetime.combine(day, t, tzinfo=tz)


def sample(points: list[tuple[datetime, float]], at: datetime, tol: timedelta = timedelta(minutes=5)) -> float | None:
    """The last mark at or before `at` within `tol`, else None. Hourly candles pass tol=61 minutes."""
    best = None
    for ts, v in points:
        if at - tol <= ts <= at and (best is None or ts > best[0]):
            best = (ts, v)
    return None if best is None else best[1]


def fresh_quote(ts: datetime, quote_time: datetime, max_age_s: int = REF_MAX_AGE_S) -> bool:
    """A live Schwab ref counts only while its quote is at most max_age_s older than the tick that stored it (the
    executable tier's rule): on a US holiday Monday the collector stores Friday's quote under a fresh ts."""
    return ts - quote_time <= timedelta(seconds=max_age_s)


def first_between(points: list[tuple[datetime, float]], lo: datetime, hi: datetime) -> float | None:
    """The earliest point in [lo, hi). ponytail: the upper bound keeps a collector gap from passing a later
    session's price off as the open."""
    inside = [(ts, v) for ts, v in points if lo <= ts < hi]
    return min(inside)[1] if inside else None


def weekend_row(symbol: str, friday: date, marks: list[tuple[datetime, float]], refs: list[tuple[datetime, float]],
                label: Label, tol: timedelta = timedelta(minutes=5)) -> dict:
    """One (symbol, friday) row. `marks`: (ts, perp mark) or (candle close time, close). `refs`: the venue's oracle
    prints, never a Schwab quote. A sample with no mark inside the tolerance is None, never skipped."""
    row = {"symbol": symbol, "friday": friday, "p_open_pre": None, "p_open_reg": None, "ref_kind": label.ref_kind}
    for key, (_, t) in SAMPLES.items():
        row["p_" + key] = sample(marks, at(friday + timedelta(days=OFFSET[key]), t), tol)
    monday = friday + timedelta(days=3)
    if label.exchange == "KRX":  # no US stock to compare: the first oracle print once Seoul opens
        row["p_open_pre"] = first_between(refs, at(monday, OPEN_KRX, KST), at(monday, CLOSE_KRX, KST))
        row["ref_kind"] = "oracle_krx"
    elif label.exchange.strip().upper() in _US:
        monday_open = at(monday, OPEN_REG)
        row["p_open_pre"] = first_between(refs, at(monday - timedelta(days=1), OPEN_PRE), monday_open)
        row["p_open_reg"] = first_between(refs, monday_open, at(monday, CLOSE_REG))
        row["ref_kind"] = "oracle_us"
    else:
        row["ref_kind"] = "none"
    return row


def _sign(x: float) -> int:
    return (x > 0) - (x < 0)


def _open(r: dict) -> float | None:
    """The row's open: the regular open, else a KRX row's Sunday-evening Korean open. Never a Schwab pre-market quote."""
    if r["p_open_reg"] is not None:
        return r["p_open_reg"]
    return r.get("p_open_pre") if r.get("ref_kind") == "oracle_krx" else None


def contained(row: dict) -> float | None:
    """How much of Monday's move the Sunday perp already held: (Sunday - Friday) / (open - Friday). None when a price is
    missing or the move is under FLOOR_BPS (noise). The open is `_open`'s, so a Schwab pre-market quote never counts."""
    fri, sun, opn = row["p_fri"], row["p_sun"], _open(row)
    if None in (fri, sun, opn) or not fri > 0 or abs(opn - fri) / fri * 1e4 < FLOOR_BPS:
        return None
    return (sun - fri) / (opn - fri)


def summary(rows: list[dict]) -> dict:
    """Did the weekend perp lean the way Monday opened, and how much of the move had it already made?"""
    cs = [c for c in map(contained, rows) if c is not None]
    rows = [{**r, "p_open_reg": _open(r)} for r in rows]
    def hit_rate(key: str) -> float | None:
        pairs = [(r[key], r["p_open_reg"], r["p_fri"]) for r in rows
                 if None not in (r[key], r["p_open_reg"], r["p_fri"]) and r["p_open_reg"] != r["p_fri"]]
        return sum(_sign(p - f) == _sign(o - f) for p, o, f in pairs) / len(pairs) if pairs else None

    return {"weekends": len(rows), "names": len({r["symbol"] for r in rows}),
            "hit_rate_sun": hit_rate("p_sun"), "hit_rate_sat": hit_rate("p_sat"),
            "contained_sun_median": median(cs) if cs else None,
            "n_sun": len(cs), "enough": len(cs) >= ENOUGH}


def fridays(end: datetime, days: int = 60) -> list[date]:
    """Oldest first: each Friday whose Monday 10:00 ET has passed by `end` (the open has printed) and whose 8 PM
    sample falls inside the last `days` days, the candle backfill's reach."""
    out, d = [], (end.astimezone(ET).date() - timedelta(days=days))
    d += timedelta(days=(4 - d.weekday()) % 7)
    while at(d + timedelta(days=3), time(10, 0)) <= end:
        if at(d, SAMPLES["fri"][1]) >= end - timedelta(days=days):
            out.append(d)
        d += timedelta(days=7)
    return out
