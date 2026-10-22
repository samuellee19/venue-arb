import re
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

from venue_arb.flows import stock_perp_backfill as bf
from venue_arb.sources.hyperliquid import Label
from venue_arb.stock_perp_window import score_window, stock_perp_opportunity_row

T0 = datetime(2026, 10, 22, 14, 0, tzinfo=UTC)
TICKS = [T0 + timedelta(seconds=60 * i) for i in range(4)]
START, END = T0 - timedelta(hours=1), T0 + timedelta(hours=1)
LABELS = {"xyz:NVDA": Label("xyz:NVDA", "schwab", "NVDA", "NASDAQ", "USD", "NVIDIA"),
          "xyz:XYZ100": Label("xyz:XYZ100", "none", None, "", "USD", "index")}


def ctx(sym, t, mark, oracle):
    return {"ts": t, "symbol": sym, "mark": mark, "oracle": oracle, "mid": mark, "funding_h": 0.0, "premium": 0.0,
            "open_interest": 1.0, "day_volume_usd": 1.0, "growth_mode": 1}


def book(sym, t, bid, ask):
    return [{"ts": t, "symbol": sym, "side": s, "level": 0, "price": p, "size": 1000.0}
            for s, p in (("bid", bid), ("ask", ask))]


def ref(sym, t, bid, ask, quote_time=None):
    return {"ts": t, "symbol": sym, "ref_kind": "schwab", "ref_symbol": "NVDA", "bid": bid, "ask": ask,
            "bid_size": 500.0, "ask_size": 500.0, "last": None, "quote_time": quote_time or t}


def window(ref_missing=()):
    # NVDA: perp bid 100.2 vs stock 99.99/100.01 (about 20 bps over); XYZ100: no stock reference
    ctxs = [ctx(s, t, m, o) for t in TICKS for s, m, o in (("xyz:NVDA", 100.2, 100.0), ("xyz:XYZ100", 50.0, 50.0))]
    books = [r for t in TICKS for s, b, a in (("xyz:NVDA", 100.2, 100.21), ("xyz:XYZ100", 49.99, 50.01))
             for r in book(s, t, b, a)]
    refs = [ref("xyz:NVDA", t, 99.99, 100.01) for t in TICKS if t not in ref_missing]
    return score_window(ctxs, books, refs, LABELS, start=START, end=END)


def test_score_window_episode_oracle_only_and_stats():
    rows, eps, stats = window()
    nvda = [r for r in rows if r["symbol"] == "xyz:NVDA"]
    assert {r["ref_kind"] for r in nvda} == {"schwab"} and {r["direction"] for r in nvda} == {"short_perp_long_stock"}
    assert all(r["net_bps"] > 10 and r["capacity_usd"] >= 100 for r in nvda)
    oracle = [r for r in rows if r["symbol"] == "xyz:XYZ100"]
    assert len(oracle) == 4 and {r["ref_kind"] for r in oracle} == {"oracle"}
    assert all(r["capacity_contracts"] == 0 for r in oracle)
    [ep] = eps
    assert (ep.kalshi_id, ep.pm_id, ep.direction, ep.snapshots) == ("xyz:NVDA", "NVDA", "short_perp_long_stock", 4)
    assert (ep.start, ep.end) == (TICKS[0], TICKS[-1]) and ep.annualized > 0
    assert stats == {"symbols": 2, "snapshots": 8, "edge_rows": 8, "rows_with_capacity": 4, "episodes": 1,
                     "oracle_only_rows": 4}


def test_missing_or_stale_ref_falls_back_to_oracle():
    rows, eps, stats = window(ref_missing={TICKS[1]})
    assert [r["ref_kind"] for r in rows if r["symbol"] == "xyz:NVDA"] == ["schwab", "oracle", "schwab", "schwab"]
    assert [(e.start, e.end, e.snapshots) for e in eps] == [(TICKS[2], TICKS[3], 2)]  # the oracle tick breaks the run
    assert stats["oracle_only_rows"] == 5
    ctxs = [ctx("xyz:NVDA", TICKS[0], 100.2, 100.0)]
    stale = [ref("xyz:NVDA", TICKS[0], 99.99, 100.01, quote_time=TICKS[0] - timedelta(seconds=300))]
    [row], _, _ = score_window(ctxs, book("xyz:NVDA", TICKS[0], 100.2, 100.21), stale, LABELS, start=START, end=END)
    assert row["ref_kind"] == "oracle"


def test_daily_refs_and_unlabelled_symbols_are_ignored_or_oracle():
    daily = {**ref("xyz:NVDA", TICKS[0], 100.0, 100.0), "ref_kind": "schwab_daily"}
    [row], _, _ = score_window([ctx("xyz:NEW", TICKS[0], 10.0, 10.0)], book("xyz:NEW", TICKS[0], 9.99, 10.01), [daily],
                               LABELS, start=START, end=END)
    assert row["symbol"] == "xyz:NEW" and row["ref_kind"] == "oracle"
    [row], _, _ = score_window([ctx("xyz:NVDA", TICKS[0], 100.2, 100.0)], book("xyz:NVDA", TICKS[0], 100.2, 100.21),
                               [daily], LABELS, start=START, end=END)
    assert row["ref_kind"] == "oracle"


def test_opportunity_row_is_not_us_executable():
    _, [ep], _ = window()
    row = stock_perp_opportunity_row(ep)
    assert (row["kalshi_id"], row["pm_id"], row["source"]) == ("xyz:NVDA", "NVDA", "stock_perp")
    assert row["us_executable"] is False and row["transfer_cost_usd"] == 0.0
    assert row["start_ts"] == ep.start and row["snapshots"] == 4
    assert set(row) == {"kalshi_id", "pm_id", "direction", "start_ts", "end_ts", "snapshots", "max_edge",
                        "median_capacity_usd", "annualized", "us_executable", "transfer_cost_usd", "source"}


def test_serve_lists_stock_perp_deployment():
    text = Path("venue_arb/flows/serve.py").read_text()
    assert re.search(r'stock_perp_score\w*\.to_deployment\("stock_perp_score", cron="55 \* \* \* \*"', text)


def test_candle_rows_join_funding_by_hour():
    h0 = datetime(2026, 10, 22, 14, tzinfo=UTC)
    candles = [{"hour": h0 + timedelta(hours=i), "open": 1.0, "high": 2.0, "low": 0.5, "close": 1.5, "volume": 3.0}
               for i in range(2)]
    funding = {h0: 1e-5}
    assert bf.candle_rows("xyz:NVDA", candles, funding) == [
        ["xyz:NVDA", h0, 1.0, 2.0, 0.5, 1.5, 3.0, 1e-5],
        ["xyz:NVDA", h0 + timedelta(hours=1), 1.0, 2.0, 0.5, 1.5, 3.0, None]]


def test_funding_pages_until_empty():
    h0 = datetime(2026, 10, 1, tzinfo=UTC)
    pages = {h0: [{"hour": h0 + timedelta(hours=i), "rate": 1e-5 * i, "premium": 0.0} for i in range(3)],
             h0 + timedelta(hours=3): [{"hour": h0 + timedelta(hours=3), "rate": 9e-5, "premium": 0.0}]}
    calls = []

    def fetch(sym, start):
        calls.append(start)
        return pages.get(start, [])

    got = bf.funding_by_hour("xyz:NVDA", h0, h0 + timedelta(days=1), fetch)
    assert sorted(got) == [h0 + timedelta(hours=i) for i in range(4)] and got[h0 + timedelta(hours=3)] == 9e-5
    assert calls == [h0, h0 + timedelta(hours=3), h0 + timedelta(hours=4)]


def test_schwab_daily_rows_skip_existing_and_stamp_the_new_york_open():
    days = [{"day": date(2026, 10, 20), "open": 10.0, "close": 11.0},  # EDT: 13:30 UTC
            {"day": date(2026, 12, 21), "open": 12.0, "close": 13.0}]  # EST: 14:30 UTC
    edt, est = datetime(2026, 10, 20, 13, 30, tzinfo=UTC), datetime(2026, 12, 21, 14, 30, tzinfo=UTC)
    assert [r[0] for r in bf.schwab_rows("xyz:NVDA", "NVDA", days, existing=set())] == [edt, est]
    [row] = bf.schwab_rows("xyz:NVDA", "NVDA", days, existing={edt})
    assert row == [est, "xyz:NVDA", "schwab_daily", "NVDA", 12.0, 12.0, 0.0, 0.0, 13.0, est]


def test_a_tick_in_the_other_direction_ends_the_run():
    ticks = [T0, T0 + timedelta(seconds=60), T0 + timedelta(seconds=120)]  # A short, B long, C short
    marks = [(100.2, 100.2, 100.21), (99.8, 99.79, 99.8), (100.2, 100.2, 100.21)]  # mark, bid, ask
    ctxs = [ctx("xyz:NVDA", t, m, 100.0) for t, (m, _, _) in zip(ticks, marks, strict=True)]
    books = [r for t, (_, b, a) in zip(ticks, marks, strict=True) for r in book("xyz:NVDA", t, b, a)]
    refs = [ref("xyz:NVDA", t, 99.99, 100.01) for t in ticks]
    rows, eps, _ = score_window(ctxs, books, refs, LABELS, start=START, end=END)
    assert [r["direction"] for r in rows] == ["short_perp_long_stock", "long_perp_short_stock", "short_perp_long_stock"]
    assert all(r["net_bps"] > 10 and r["capacity_usd"] >= 100 for r in rows)  # each tick qualifies alone
    assert eps == []  # A and C are not bridged across B
