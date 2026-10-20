import threading
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from types import SimpleNamespace

from venue_arb.collect import WATCH_SQL, flush, tick
from venue_arb.models import Level

WATCH = [("kalshi", "K1", None), ("polymarket", "P1", "T1")]


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
    expect_schema=lambda rows, contract, target: Result("expect_schema", True, target=target),
)


def run(fetchers, load, max_buffer=1000, watch=WATCH, **kw):
    buf = {"kalshi": [], "polymarket": []}
    stats = {"kalshi": [0, 0, 0], "polymarket": [0, 0, 0]}
    tick(watch, fetchers, buf, stats)
    recorded = []
    flush(buf, {"kalshi", "polymarket"}, load, recorded.extend, list, FAKE_CHECKS, stats, max_buffer, **kw)
    return buf, stats, recorded


def kalshi_ok(mid):
    return [Level("yes", 0, 0.4, 10.0), Level("no", 0, 0.6, 5.0)]


def test_collect_one_venue_down():
    def pm_down(token):
        raise TimeoutError("down")

    loaded = []
    buf, stats, recorded = run({"kalshi": kalshi_ok, "polymarket": pm_down}, loaded.append)

    assert [r["venue"] for rows in loaded for r in rows] == ["kalshi", "kalshi"]
    failed = {(r.check, r.target) for r in recorded if not r.passed}
    assert failed == {("expect_rows", "venue_arb.book_levels_v2:polymarket"),
                      ("fetch_errors", "venue_arb.book_levels_v2:polymarket")}
    assert buf == {"kalshi": [], "polymarket": []} and stats["polymarket"] == [0, 0, 0]


def test_load_failure_recorded_other_venue_loads_buffer_bounded():
    loaded = []

    def load(rows):
        if rows[0]["venue"] == "kalshi":
            raise ConnectionError("ch down")
        loaded.append(rows)

    buf, _, recorded = run({"kalshi": kalshi_ok, "polymarket": kalshi_ok}, load, max_buffer=1)
    assert [(r.check, r.target) for r in recorded if not r.passed] == [("load", "venue_arb.book_levels_v2:kalshi")]
    assert "ConnectionError" in next(r for r in recorded if r.check == "load").detail
    assert len(loaded) == 1 and len(buf["kalshi"]) == 1 and buf["polymarket"] == []


def test_empty_book_counted_and_warned():
    loaded = []
    _, _, recorded = run({"kalshi": kalshi_ok, "polymarket": lambda token: []}, loaded.append)
    empty = {r.target: r for r in recorded if r.check == "empty_books"}
    assert empty["venue_arb.book_levels_v2:polymarket"].observed == 1
    assert not empty["venue_arb.book_levels_v2:polymarket"].passed
    assert empty["venue_arb.book_levels_v2:polymarket"].severity == "warn"
    assert empty["venue_arb.book_levels_v2:kalshi"].passed
    fetch = {r.target: r for r in recorded if r.check == "fetch_errors"}
    assert fetch["venue_arb.book_levels_v2:polymarket"].passed  # empty is not an error


def test_rows_stamped_per_fetch():
    def slow(mid):
        time.sleep(0.02)
        return kalshi_ok(mid)

    watch = [("kalshi", f"K{i}", None) for i in range(3)]
    loaded = []
    before = datetime.now(UTC)
    run({"kalshi": slow, "polymarket": slow}, loaded.append, watch=watch)
    stamps = sorted({r["ts"] for rows in loaded for r in rows})
    assert len(stamps) == 3 and stamps[0] > before  # one stamp per book, taken after its fetch


def test_concurrent_fetch_accounting():
    seen, lock = set(), threading.Lock()

    def flaky(mid):
        with lock:
            seen.add(threading.get_ident())
        time.sleep(0.01)
        if int(mid[1:]) % 3 == 0:
            raise TimeoutError
        return [] if int(mid[1:]) % 3 == 1 else kalshi_ok(mid)

    watch = [("kalshi", f"K{i}", None) for i in range(30)]
    buf, stats = {"kalshi": [], "polymarket": []}, {"kalshi": [0, 0, 0], "polymarket": [0, 0, 0]}
    tick(watch, {"kalshi": flaky}, buf, stats, workers=8)
    assert stats["kalshi"] == [30, 10, 10] and len(buf["kalshi"]) == 20 and len(seen) > 1


def test_cycle_duration_recorded():
    _, _, recorded = run({"kalshi": kalshi_ok, "polymarket": kalshi_ok}, lambda rows: None,
                         cycles=[40.0, 75.5], interval_s=60)
    cyc = next(r for r in recorded if r.check == "cycle_seconds")
    assert (cyc.passed, cyc.observed, cyc.severity) == (False, 75.5, "warn")
    _, _, ok = run({"kalshi": kalshi_ok, "polymarket": kalshi_ok}, lambda rows: None, cycles=[30.0], interval_s=60)
    assert next(r for r in ok if r.check == "cycle_seconds").passed


def test_watchlist_sql_keeps_live_markets_at_current_versions():
    sql = " ".join(WATCH_SQL.split())
    for leg in ("la", "lb"):
        assert f"{leg}.last_seen > now() - INTERVAL '2 hours'" in sql and f"{leg}.close_time > now()" in sql
    assert "a_version = la.version" in sql and "b_version = lb.version" in sql


def test_default_fetchers_skip_polite_delay(monkeypatch):
    from venue_arb import collect, http

    calls = []
    monkeypatch.setattr(http, "get_json", lambda url, params=None, **kw: calls.append(kw) or {})
    f = collect.default_fetchers()
    assert f["kalshi"]("K1") == [] and f["polymarket"]("T1") == []
    assert calls == [{"delay": 0}, {"delay": 0}]


def _listing(venue, mid, label, close, event="KXBTCD-26OCT2004", title="t", token=None):
    return (venue, mid, event, "crypto_price", title, label, close, token)


class _Conn:
    def __init__(self, listings, fail=False):
        self.listings, self.fail = listings, fail

    def execute(self, sql):
        if "venue_arb.listings WHERE category" in sql:
            if self.fail:
                raise RuntimeError("boom")
            return SimpleNamespace(fetchall=lambda: self.listings)
        return SimpleNamespace(description=[], fetchall=list)  # no candidate pairs


class _Ch:
    def __init__(self, rows):
        self.rows = rows

    def query(self, sql):
        return SimpleNamespace(result_rows=self.rows)


def _ladder_listings():
    t = datetime(2026, 10, 20, 8, tzinfo=UTC)  # 4 AM EDT
    k = [_listing("kalshi", f"K{x}", f"${x:,} or above", t) for x in (79000, 80000, 81000, 90000)]
    p = [_listing("polymarket", f"P{x}", "Yes", t, f"e{x}", f"Bitcoin above {x:,} on October 20, 4AM ET?", f"T{x}")
         for x in (80000, 81000)]
    return k + p


def test_load_watchlist_adds_clipped_ladders_with_basis_pad():
    from venue_arb.collect import load_watchlist
    wl = load_watchlist(_Conn(_ladder_listings()), _Ch([("BTC", 30.0)] * 30))  # bounds 30 -> pad 30
    assert {m for _, m, _ in wl} == {"K80000", "K81000", "P80000", "P81000"}  # 79000 < 80000-30, 90000 too far
    wl = load_watchlist(_Conn(_ladder_listings()), _Ch([]))  # < 24 rows: default pad 50
    assert {m for _, m, _ in wl} == {"K80000", "K81000", "P80000", "P81000"}
    assert ("polymarket", "P80000", "T80000") in wl and ("kalshi", "K80000", None) in wl


def test_load_watchlist_ladder_failure_falls_back():
    from venue_arb.collect import load_watchlist
    assert load_watchlist(_Conn(_ladder_listings(), fail=True), _Ch([])) == []
