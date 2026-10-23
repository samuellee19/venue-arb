from datetime import UTC, date, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from venue_arb import weekends as wk
from venue_arb.sources.hyperliquid import Label

ET = ZoneInfo("America/New_York")
FRI = date(2026, 10, 16)  # EDT
LABEL_SCHWAB = Label("xyz:NVDA", "schwab", "NVDA", "NASDAQ", "USD", "NVIDIA")
LABEL_NONE = Label("xyz:XYZ100", "none", None, "", "USD", "index")
LABEL_KRX = Label("xyz:SKHX", "none", "000660", "KRX", "KRW", "SK hynix")


def et(day, h, m=0):
    return datetime(day.year, day.month, day.day, h, m, tzinfo=ET)


SAT, SUN, MON = FRI + timedelta(1), FRI + timedelta(2), FRI + timedelta(3)
MARKS = [(et(FRI, 19, 58), 212.0), (et(FRI, 20, 0), 213.0), (et(SAT, 11, 59), 214.0), (et(SUN, 17, 59), 215.0)]
MARKS_WITHOUT_SUNDAY = MARKS[:3]
REFS = [(et(SUN, 20, 4), 999.0), (et(SUN, 20, 5), 214.5), (et(MON, 9, 29), 214.9), (et(MON, 9, 30), 215.2),
        (et(MON, 9, 31), 216.0)]


def test_sample_last_point_within_tolerance():
    at = et(FRI, 20, 0)
    pts = [(at - timedelta(minutes=9), 1.0), (at - timedelta(minutes=3), 2.0), (at - timedelta(minutes=1), 3.0),
           (at + timedelta(minutes=1), 4.0)]
    assert wk.sample(pts, at) == 3.0
    assert wk.sample(pts[:1], at) is None  # older than 5 minutes
    assert wk.sample(pts[:1], at, tol=timedelta(minutes=61)) == 1.0
    assert wk.sample(pts[3:], at) is None  # only after


def test_missing_sample_is_null_not_skipped():
    row = wk.weekend_row("xyz:NVDA", FRI, MARKS_WITHOUT_SUNDAY, REFS, LABEL_SCHWAB)
    assert row["p_sun"] is None and row["p_fri"] == 213.0 and row["p_sat"] == 214.0 and row["p_open_reg"] == 215.2
    assert row["friday"] == FRI and row["ref_kind"] == "oracle_us"


def test_public_weekend_rows_use_oracle_only():
    row = wk.weekend_row("xyz:NVDA", FRI, MARKS, REFS, LABEL_SCHWAB)
    assert row["ref_kind"] == "oracle_us"
    assert row["p_open_pre"] == 214.5 and row["p_open_reg"] == 215.2  # Sun 20:05 ET print; Mon 09:30 ET print
    assert "daily" not in wk.weekend_row.__code__.co_varnames
    for ex in ("NYSE", "nyse arca", "Cboe BZX"):
        assert wk.weekend_row("s", FRI, MARKS, REFS, Label("s", "none", "X", ex, "USD", "x"))["ref_kind"] == "oracle_us"


def test_oracle_us_without_prints_has_no_opens():
    row = wk.weekend_row("xyz:NVDA", FRI, MARKS, [], LABEL_SCHWAB)
    assert row["p_open_pre"] is None and row["p_open_reg"] is None and row["ref_kind"] == "oracle_us"


def test_flow_no_longer_reads_refs():
    from pathlib import Path
    text = Path("venue_arb/flows/stock_perp_weekend.py").read_text()
    assert "stock_perp_refs" not in text and "schwab" not in text
    assert "ref_kind = 'oracle_us' AND p_open_reg IS NOT NULL" in text and "ref_kind = 'oracle_krx' AND p_open_pre" in text


def test_marks_may_be_hourly_with_wide_tolerance():
    hourly = [(et(FRI, 19, 0).astimezone(UTC), 213.5)]  # 60 minutes before the sample
    assert wk.weekend_row("s", FRI, hourly, [], LABEL_NONE)["p_fri"] is None
    assert wk.weekend_row("s", FRI, hourly, [], LABEL_NONE, tol=timedelta(minutes=61))["p_fri"] == 213.5


def test_none_label_has_no_opens():
    row = wk.weekend_row("xyz:XYZ100", FRI, MARKS, REFS, LABEL_NONE)
    assert (row["p_open_pre"], row["p_open_reg"], row["ref_kind"]) == (None, None, "none") and row["p_sun"] == 215.0


def test_krx_label_uses_sunday_evening_oracle():
    seoul = ZoneInfo("Asia/Seoul")
    open_ = datetime(MON.year, MON.month, MON.day, 9, 0, tzinfo=seoul)
    assert open_.astimezone(ET) == et(SUN, 20, 0)  # EDT: 09:00 KST is Sunday 20:00 ET
    oracle = [(open_ - timedelta(minutes=1), 1.0), (open_ + timedelta(minutes=2), 2.0), (open_ + timedelta(minutes=1), 3.0)]
    row = wk.weekend_row("xyz:SKHX", FRI, MARKS, oracle, LABEL_KRX)
    assert (row["p_open_pre"], row["p_open_reg"], row["ref_kind"]) == (3.0, None, "oracle_krx")
    winter = date(2026, 11, 20)  # EST: the same 09:00 KST is Sunday 19:00 ET, so it is computed in Seoul time
    o = datetime(2026, 11, 23, 9, 0, tzinfo=seoul)
    assert wk.weekend_row("xyz:SKHX", winter, [], [(o, 7.0)], LABEL_KRX)["p_open_pre"] == 7.0
    assert o.astimezone(ET).hour == 19


def row(fri, sat, sun, reg, sym="a", pre=None, kind="schwab"):
    return {"symbol": sym, "p_fri": fri, "p_sat": sat, "p_sun": sun, "p_open_pre": pre, "p_open_reg": reg,
            "ref_kind": kind}


def test_summary_hit_rate_and_floor():
    rows = [row(100, 101, 101, 102, f"s{i}") for i in range(17)]  # up, up: agree, 200 bps
    rows += [row(100, 99, 99, 102, f"d{i}") for i in range(5)]  # down vs up: disagree
    rows += [row(100, 100.1, 100.1, 100.05, f"f{i}") for i in range(3)]  # 5 bps: agree but under the floor
    s = wk.summary(rows)
    assert s["hit_rate_sun"] == pytest.approx(20 / 25) and s["hit_rate_sat"] == pytest.approx(20 / 25)
    assert s["n_sun"] == 22 and s["enough"] and s["weekends"] == 25 and s["names"] == 25
    assert s["contained_sun_median"] == pytest.approx(0.5)  # 17 rows at 1/2, 5 rows at -1/2


def test_summary_krx_rows_use_the_korean_open_and_schwab_pre_market_is_ignored():
    krx = [row(100, 101, 101, None, f"k{i}", pre=102, kind="oracle_krx") for i in range(3)]
    krx += [row(100, 99, 99, None, "kd", pre=102, kind="oracle_krx"), row(100, 101, 101, None, "kf", pre=100.05,
                                                                          kind="oracle_krx")]  # last: under 10 bps
    schwab_pre_only = [row(100, 101, 101, None, "s", pre=102, kind="schwab")]
    s = wk.summary(krx + schwab_pre_only)
    assert s["hit_rate_sun"] == pytest.approx(4 / 5) and s["hit_rate_sat"] == pytest.approx(4 / 5)
    assert s["n_sun"] == 4 and s["contained_sun_median"] == pytest.approx(0.5) and not s["enough"]
    assert s["weekends"] == 6 and s["names"] == 6


def test_contained_is_the_one_place_for_the_open_fallback_and_the_floor():
    assert wk.contained(row(100, 99, 101, 102)) == pytest.approx(0.5)  # (101 - 100) / (102 - 100)
    assert wk.contained(row(100, 99, 99, 102)) == pytest.approx(-0.5)
    assert wk.contained(row(100, 100.1, 100.1, 100.05)) is None  # 5 bps: under the floor
    assert wk.contained(row(100, 100, 101, 100)) is None  # an unchanged open
    assert wk.contained(row(100, 101, None, 102)) is None and wk.contained(row(100, 101, 101, None)) is None
    assert wk.contained(row(0, 0, 1, 2)) is None
    assert wk.contained(row(100, 101, 101, None, pre=102, kind="oracle_krx")) == pytest.approx(0.5)  # the Korean open
    assert wk.contained(row(100, 101, 101, None, pre=102, kind="schwab")) is None  # never a Schwab pre-market quote


def test_summary_empty_and_nulls():
    s = wk.summary([row(100, None, None, None), row(100, 100, 100, 100)])  # a null, then an unchanged open
    assert s == {"weekends": 2, "names": 1, "hit_rate_sun": None, "hit_rate_sat": None,
                 "contained_sun_median": None, "n_sun": 0, "enough": False}


def test_fridays_are_completed_weekends_in_the_window():
    end = datetime(2026, 10, 19, 15, 0, tzinfo=UTC)  # Monday 11:00 EDT: the 16th is complete
    fs = wk.fridays(end)
    assert fs[-1] == FRI and all(f.weekday() == 4 for f in fs) and fs[0] == date(2026, 8, 21)
    assert wk.fridays(datetime(2026, 10, 19, 13, 59, tzinfo=UTC))[-1] == date(2026, 10, 9)  # before 10:00 EDT
    assert wk.fridays(datetime(2026, 11, 23, 15, 0, tzinfo=UTC))[-1] == date(2026, 11, 20)  # 10:00 EST is 15:00 UTC


def test_weekend_flow_is_served_monday_15_utc():
    from pathlib import Path
    text = Path("venue_arb/flows/serve.py").read_text()
    assert 'stock_perp_weekend_flow.to_deployment("stock_perp_weekend", cron="0 15 * * 1"' in text


def test_fall_back_weekend_resolves_to_the_right_utc_instants():
    # Friday 30 Oct 2026 20:00 EDT; the clocks fall back early Sunday 1 Nov, so Sunday 18:00 and Monday are EST.
    fri = date(2026, 10, 30)

    def u(day, h, m=0):  # day 31 is Oct 31, 32 is Nov 1, 33 is Nov 2
        return datetime(2026, 10 + (day > 31), day - 31 * (day > 31), h, m, tzinfo=UTC)

    marks = [(u(31, 0), 1.0), (u(31, 16), 2.0), (u(32, 23), 3.0),  # Fri 20:00 EDT, Sat 12:00 EDT, Sun 18:00 EST
             (u(32, 22), 99.0)]  # an hour early: what an EDT-assuming Sunday 18:00 would be if it were 17:00 EST
    refs = [(u(33, 1, 4), 98.0), (u(33, 1, 5), 4.0), (u(33, 14, 29), 97.0), (u(33, 14, 30), 5.0)]
    # Sunday 20:05 EST is 01:05 UTC Monday (an EDT-assuming 20:05 would be 00:05); Monday 09:30 EST is 14:30 UTC
    r = wk.weekend_row("xyz:NVDA", fri, marks, refs, LABEL_SCHWAB)
    assert (r["p_fri"], r["p_sat"], r["p_sun"], r["p_open_pre"], r["p_open_reg"]) == (1.0, 2.0, 3.0, 4.0, 5.0)
    assert wk.at(fri, wk.SAMPLES["fri"][1]).astimezone(UTC) == u(31, 0)
    assert wk.at(date(2026, 11, 1), wk.SAMPLES["sun"][1]).astimezone(UTC) == u(32, 23)
    assert wk.at(date(2026, 11, 1), wk.OPEN_PRE).astimezone(UTC) == u(33, 1, 5)
    assert wk.at(date(2026, 11, 2), wk.OPEN_REG).astimezone(UTC) == u(33, 14, 30)


def test_holiday_monday_keeps_only_external_oracle_prints():
    # Labor Day 2026-09-07: the oracle is internal all Monday (it follows the perp), so no regular open
    fri = date(2026, 9, 4)
    mon = fri + timedelta(3)
    refs = [(et(mon, 9, 30), 216.0), (et(mon, 10, 0), 217.0)]
    row = wk.weekend_row("xyz:NVDA", fri, MARKS, refs, LABEL_SCHWAB)
    assert row["p_open_reg"] is None and row["p_open_pre"] is None and row["ref_kind"] == "oracle_us"
    assert wk.contained(row) is None
