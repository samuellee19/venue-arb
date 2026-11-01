import re
from datetime import UTC, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from venue_arb.flows import stock_perp_backfill as bf

H = timedelta(hours=1)
NOW = datetime(2026, 11, 2, 14, 0, 7, tzinfo=UTC)  # a Monday run, a few seconds past the hour
END = datetime(2026, 11, 2, 14, tzinfo=UTC)


class FakeCH:
    """The ReplacingMergeTree as FINAL reads it: one row per (symbol, hour), the last insert wins."""

    def __init__(self):
        self.inserts, self.final = 0, {}

    def insert(self, table, rows, column_names):
        assert table == bf.CANDLES and column_names == bf.CANDLE_COLS
        self.inserts += 1
        self.final.update({(r[0], r[1]): r for r in rows})


def fake_venue(monkeypatch, fail=()):
    calls = []

    def candles(symbol, start, end):
        calls.append(("candleSnapshot", symbol))
        if symbol in fail:
            raise RuntimeError("POST info candleSnapshot failed after 4 tries: HTTP 429")
        # the venue also returns the hour still open at `end`
        return [{"hour": h, "open": 1.0, "high": 2.0, "low": 0.5, "close": 1.5, "volume": 3.0}
                for h in (start + H * i for i in range(int((end - start) / H) + 1))]

    def funding(symbol, start):
        calls.append(("fundingHistory", symbol))
        return [{"hour": h, "rate": 1e-5, "premium": 0.0} for h in (start + H * i for i in range(500)) if h < END]

    monkeypatch.setattr(bf.hyperliquid, "fetch_candles", candles)
    monkeypatch.setattr(bf.hyperliquid, "fetch_funding", funding)
    monkeypatch.setattr(bf.clock, "sleep", lambda s: calls.append(("sleep", s)))
    return calls


def test_window_is_whole_hours_and_leaves_out_the_open_one():
    pytest.importorskip("obs")  # the flow module imports the platform's obs and qa; the public CI has neither
    pytest.importorskip("qa")
    from venue_arb.flows.stock_perp_candles import window

    start, end = window(NOW)
    assert (start, end) == (END - timedelta(days=4), END)
    assert window(NOW.astimezone(ZoneInfo("America/Los_Angeles")), days=11)[0] == END - timedelta(days=11)


def test_reload_of_a_window_writes_the_same_keys(monkeypatch):
    fake_venue(monkeypatch)
    ch, start = FakeCH(), END - timedelta(days=4)
    assert bf.load_candles(ch, "xyz:NVDA", start, END) == 96  # the open 14:00 hour is not stored
    first = dict(ch.final)
    assert bf.load_candles(ch, "xyz:NVDA", start, END) == 96
    assert ch.final == first and len(first) == 96 and ch.inserts == 2
    assert max(h for _, h in first) == END - H and all(r[-1] == 1e-5 for r in first.values())


def test_paced_load_batches_inside_the_budget_and_names_failures(monkeypatch):
    calls = fake_venue(monkeypatch, fail={"s3"})
    symbols = [f"s{i}" for i in range(25)]
    loaded, failed = bf.load_paced(FakeCH(), symbols, END - timedelta(days=4), END, log=lambda m: None)
    assert failed == ["s3"] and len(loaded) == 24 and set(loaded.values()) == {96}
    assert [c for c in calls if c[0] == "sleep"] == [("sleep", bf.PAUSE_S)] * 2  # 10 + 10 + 5
    assert bf.request_weight(96) == 67 and 10 * 67 <= bf.PER_MINUTE  # the arithmetic in the flow docstring


def test_served_monday_an_hour_before_the_weekend_flow():
    text = (Path(__file__).parents[1] / "venue_arb/flows/serve.py").read_text()
    assert 'stock_perp_candles_flow.to_deployment("stock_perp_candles", cron="0 14 * * 1"' in text
    assert re.search(r'to_deployment\("stock_perp_weekend", cron="0 15 \* \* 1"', text)
