from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

from venue_arb import validation as v
from venue_arb.sources.hyperliquid import Label

NV = Label("xyz:NVDA", "schwab", "NVDA", "NASDAQ", "USD", "NVIDIA")
KR = Label("xyz:SKHX", "none", "000660", "KRX", "KRW", "SK hynix")


@dataclass
class Result:  # stands in for qa.checks.Result (qa is not installed in the unit-test env)
    name: str
    passed: bool
    count: int = 0
    detail: str = ""
    severity: str = "error"
    target: str = ""


checks = type("checks", (), {"Result": Result})
LABELS = {"xyz:NVDA": NV, "xyz:SKHX": KR}
T0 = datetime(2026, 10, 21, 15, 0, tzinfo=UTC)  # Wednesday 11:00 ET


def tick(i, orc=100.0, bid=100.0, ask=100.0, age=0, sym="xyz:NVDA", t0=T0):
    ts = t0 + timedelta(minutes=i)
    return ({"symbol": sym, "ts": ts, "oracle": orc},
            {"symbol": sym, "ts": ts, "bid": bid, "ask": ask, "quote_time": ts - timedelta(seconds=age)})


def run(ticks):
    return v.compare([c for c, _ in ticks], [r for _, r in ticks], LABELS)


def test_compare_skips_stale_and_one_sided_quotes():
    ticks = [tick(0), tick(1, age=300), tick(2, bid=0), tick(3, bid=101, ask=100), tick(4, sym="xyz:SKHX")]
    (r,) = run(ticks)
    assert r["n"] == 1 and r["symbol"] == "xyz:NVDA" and r["day"] == date(2026, 10, 21)
    sat = tick(0, t0=datetime(2026, 10, 17, 15, 0, tzinfo=UTC))  # Saturday: internal session
    assert run([sat]) == []


def test_compare_percentiles():
    ticks = [tick(i, orc=100 + i * 0.01) for i in range(20)]  # 0..19 bps, mid 100
    (r,) = run(ticks)
    assert r["n"] == 20 and abs(r["median_abs_bps"] - 9.5) < 1e-6
    assert abs(r["p95_abs_bps"] - 18) < 1e-6 and r["share_over_10"] == 0.45 and r["open_gap_bps"] is None


def test_monday_open_gap():
    mon = datetime(2026, 10, 19, 13, 30, tzinfo=UTC)  # 09:30 EDT
    ticks = [tick(0, orc=100.1, bid=100, ask=100, t0=mon), tick(1, orc=105, t0=mon)]
    (r,) = run(ticks)
    assert abs(r["open_gap_bps"] - 10) < 1e-6


def test_drift_warn_threshold():
    d = date(2026, 10, 21)
    rows = [{"day": d, "symbol": "a", "p95_abs_bps": 10.0}, {"day": d, "symbol": "b", "p95_abs_bps": 4},
            {"day": d - timedelta(days=1), "symbol": "b", "p95_abs_bps": 10.5},
            {"day": d - timedelta(days=5), "symbol": "c", "p95_abs_bps": 50}]  # outside the 5-day window
    (res,) = v.drift_results(checks, rows)
    assert res.name == "oracle_drift" and res.severity == "warn" and "b" in res.detail and not res.passed


def test_publisher_never_reads_the_check_table():
    for f in ("publish.py", "bundle.py"):
        assert "oracle_check" not in Path("venue_arb", f).read_text()


def test_monday_open_gap_first_usable_tick_within_five_minutes():
    mon = datetime(2026, 10, 19, 13, 32, tzinfo=UTC)  # 09:32 EDT
    (r,) = run([tick(0, orc=100.1, t0=mon), tick(1, orc=105, t0=mon)])
    assert abs(r["open_gap_bps"] - 10) < 1e-6
    late = datetime(2026, 10, 19, 13, 35, tzinfo=UTC)  # 09:35: too late
    (r,) = run([tick(0, orc=100.1, t0=late)])
    assert r["open_gap_bps"] is None


def test_oracle_check_report_states_its_coverage():  # the flow imports the platform stack, so read its source
    import re

    text = re.sub(r'"\s*\n\s*"', "", Path("venue_arb/flows/stock_perp_oracle_check.py").read_text())  # join literals
    for phrase in ("US-listed names only", "fresh two-sided Schwab quote", "about 04:00-20:00 ET",
                   "overnight external hours (20:00-04:00 ET) are not validated",
                   "newest day is partial and is rewritten by the next run"):
        assert phrase in text, phrase
