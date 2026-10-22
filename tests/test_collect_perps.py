from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

from venue_arb import collect_perps as cp
from venue_arb.sources.hyperliquid import Label, PerpCtx, PerpLevel
from venue_arb.sources.schwab import Quote, SchwabAuthError

NOW = datetime(2026, 10, 22, 18, 0, tzinfo=UTC)


@dataclass
class Result:  # minimal stand-in for qa.checks.Result (CI has no platform checkout)
    check: str
    passed: bool
    observed: float | None = None
    threshold: float | None = None
    detail: str = ""
    severity: str = "error"
    target: str = ""


FAKE_CHECKS = SimpleNamespace(
    Result=Result,
    expect_rows=lambda n, target: Result("expect_rows", n >= 1, n, 1, target=target),
    expect_schema=lambda rows, contract, target, nullable=frozenset(): Result("expect_schema", True, target=target),
)


def ctx(symbol, mid=100.0, delisted=False):
    return PerpCtx(symbol, 100.0, 100.5, mid, 1e-5, None, 10.0, 1e6, True, 10, delisted)


LABELS = {f"xyz:{s}": Label(f"xyz:{s}", "schwab", s, "NASDAQ", "USD", s) for s in ("NVDA", "AAPL")}
LABELS["xyz:GOLD"] = Label("xyz:GOLD", "none", None, "", "USD", "Gold")
CTXS = [ctx("xyz:NVDA"), ctx("xyz:AAPL", mid=None), ctx("xyz:NEWCO"), ctx("xyz:OLD", delisted=True)]
BOOK = [PerpLevel("bid", 0, 99.9, 5.0), PerpLevel("ask", 0, 100.1, 4.0)]
QUOTES = {s: Quote(s, 99.8, 100.2, 100.0, 200.0, None, NOW - timedelta(seconds=3)) for s in ("NVDA", "AAPL")}


def fresh():
    return {"books": [], "ctx": [], "refs": []}, {"books": [0, 0, 0], "ctx": [0, 0, 0], "refs": [0, 0, 0],
                                                  "schwab_auth": 0, "unlabelled": []}


def test_tick_fills_three_buffers_and_quotes_once():
    buf, stats = fresh()
    calls = []
    perps = cp.tick(None, LABELS, lambda: CTXS, lambda s: (NOW - timedelta(seconds=2), BOOK), lambda syms: calls.append(syms) or QUOTES,
                    buf, stats, NOW)
    assert calls == [["NVDA", "AAPL"]] and len(buf["ctx"]) == 3 and len(buf["books"]) == 3 * len(BOOK)
    assert {r["symbol"] for r in buf["refs"]} == {"xyz:NVDA", "xyz:AAPL"}
    assert buf["refs"][0]["quote_time"] == QUOTES["NVDA"].quote_time and buf["refs"][0]["ts"] == NOW
    assert [p.symbol for p in perps] == ["xyz:NVDA", "xyz:AAPL", "xyz:NEWCO"]  # unlabelled snapshotted, delisted not
    assert stats["unlabelled"] == ["xyz:NEWCO"] and stats["books"] == [3, 0, 0] and stats["ctx"] == [1, 0, 0]
    aapl = next(r for r in buf["ctx"] if r["symbol"] == "xyz:AAPL")
    assert aapl["mid"] is None and aapl["growth_mode"] == 1
    assert {r["ts"] for k in ("books", "ctx", "refs") for r in buf[k]} == {NOW}  # one tick time across the tables
    assert set(buf["books"][0]) == set(cp.CONTRACTS["books"]) and set(buf["ctx"][0]) == set(cp.CONTRACTS["ctx"])
    assert set(buf["refs"][0]) == set(cp.CONTRACTS["refs"])


def test_book_failure_counted_not_raised():
    buf, stats = fresh()

    def book(s):
        if s == "xyz:AAPL":
            raise TimeoutError
        return NOW, BOOK

    cp.tick(None, LABELS, lambda: CTXS, book, lambda syms: QUOTES, buf, stats, NOW)
    assert stats["books"] == [3, 1, 0] and len(buf["books"]) == 2 * len(BOOK)


def test_empty_book_counted():
    buf, stats = fresh()
    cp.tick(None, LABELS, lambda: CTXS, lambda s: (NOW, []), lambda syms: QUOTES, buf, stats, NOW)
    assert stats["books"] == [3, 0, 3] and buf["books"] == []


def test_schwab_auth_error_counted_and_perp_side_continues():
    buf, stats = fresh()

    def expired(syms):
        raise SchwabAuthError("401")

    cp.tick(None, LABELS, lambda: CTXS, lambda s: (NOW, BOOK), expired, buf, stats, NOW)
    assert buf["refs"] == [] and stats["schwab_auth"] == 1 and stats["refs"] == [0, 0, 0]
    assert len(buf["ctx"]) == 3 and len(buf["books"]) == 3 * len(BOOK)


def test_quote_failure_counted_and_missing_quote_is_empty():
    buf, stats = fresh()

    def down(syms):
        raise TimeoutError

    cp.tick(None, LABELS, lambda: CTXS, lambda s: (NOW, BOOK), down, buf, stats, NOW)
    assert stats["refs"] == [1, 1, 0] and stats["schwab_auth"] == 0
    cp.tick(None, LABELS, lambda: CTXS, lambda s: (NOW, BOOK), lambda syms: {"NVDA": QUOTES["NVDA"]}, buf, stats, NOW)
    assert stats["refs"] == [2, 1, 1] and [r["symbol"] for r in buf["refs"]] == ["xyz:NVDA"]


def test_ctx_failure_reuses_previous_catalog():
    buf, stats = fresh()
    prev = cp.tick(None, LABELS, lambda: CTXS, lambda s: (NOW, BOOK), lambda syms: QUOTES, buf, stats, NOW)
    buf, stats = fresh()

    def down():
        raise TimeoutError

    perps = cp.tick(prev, LABELS, down, lambda s: (NOW, BOOK), lambda syms: QUOTES, buf, stats, NOW)
    assert perps == prev and stats["ctx"] == [1, 1, 0] and buf["ctx"] == [] and len(buf["books"]) == 3 * len(BOOK)


def test_no_schwab_client_skips_quotes():
    buf, stats = fresh()
    cp.tick(None, LABELS, lambda: CTXS, lambda s: (NOW, BOOK), None, buf, stats, NOW)
    assert buf["refs"] == [] and stats["refs"] == [0, 0, 0]


def test_flush_results_warn_day_before_expiry():
    issued = NOW - timedelta(days=6, hours=1)
    res = {r.check: r for r in cp.flush_results(FAKE_CHECKS, {"schwab_auth": 0, "unlabelled": ["xyz:NEWCO"]}, issued, NOW)}
    assert not res["schwab_token_expires"].passed and res["schwab_token_expires"].severity == "warn"
    assert not res["unlabelled_perps"].passed and "xyz:NEWCO" in res["unlabelled_perps"].detail
    assert res["schwab_auth"].passed


def test_flush_results_healthy_and_auth_failed():
    issued = NOW - timedelta(days=1)
    res = {r.check: r for r in cp.flush_results(FAKE_CHECKS, {"schwab_auth": 0, "unlabelled": []}, issued, NOW)}
    assert all(r.passed for r in res.values())
    res = {r.check: r for r in cp.flush_results(FAKE_CHECKS, {"schwab_auth": 2, "unlabelled": []}, issued, NOW)}
    assert not res["schwab_auth"].passed and res["schwab_auth"].severity == "warn"


def test_flush_results_without_token_warns_and_skips_expiry():
    res = {r.check: r for r in cp.flush_results(FAKE_CHECKS, {"schwab_auth": 0, "unlabelled": []}, None, NOW)}
    assert not res["schwab_auth"].passed and res["schwab_auth"].detail == "SCHWAB_REFRESH_TOKEN not set"
    assert "schwab_token_expires" not in res


def test_flush_loads_each_table_and_resets_stats():
    buf, stats = fresh()
    cp.tick(None, LABELS, lambda: CTXS, lambda s: (NOW, BOOK), lambda syms: QUOTES, buf, stats, NOW)
    loaded, recorded = {}, []
    out = cp.flush(buf, lambda key: (lambda rows: loaded.setdefault(key, rows)), recorded.extend, list, FAKE_CHECKS,
                   stats, NOW - timedelta(days=1), NOW)
    assert {k: len(v) for k, v in loaded.items()} == {"books": 6, "ctx": 3, "refs": 2}
    assert {r.target for r in out if r.check == "expect_rows"} == {f"{t}:{k}" for k, t in cp.TABLES.items()}
    assert recorded == out and buf == {"books": [], "ctx": [], "refs": []}
    assert stats["books"] == [0, 0, 0] and stats["schwab_auth"] == 0


def test_flush_skips_refs_when_nothing_was_tried():
    buf, stats = fresh()
    cp.tick(None, LABELS, lambda: CTXS, lambda s: (NOW, BOOK), None, buf, stats, NOW)
    out = cp.flush(buf, lambda key: (lambda rows: None), lambda r: None, list, FAKE_CHECKS, stats, None, NOW)
    assert not [r for r in out if r.target.endswith(":refs")]
    assert next(r for r in out if r.check == "schwab_auth").detail == "SCHWAB_REFRESH_TOKEN not set"
