from dataclasses import dataclass
from datetime import UTC, datetime
from types import SimpleNamespace

from venue_arb.collect import flush, tick
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


def run(fetchers, load, max_buffer=1000):
    buf = {"kalshi": [], "polymarket": []}
    stats = {"kalshi": [0, 0], "polymarket": [0, 0]}
    tick(WATCH, fetchers, buf, datetime.now(UTC), stats)
    recorded = []
    flush(buf, {"kalshi", "polymarket"}, load, recorded.extend, list, FAKE_CHECKS, stats, max_buffer)
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
    assert failed == {("expect_rows", "venue_arb.book_levels:polymarket"),
                      ("fetch_errors", "venue_arb.book_levels:polymarket")}
    assert buf == {"kalshi": [], "polymarket": []} and stats["polymarket"] == [0, 0]


def test_load_failure_recorded_other_venue_loads_buffer_bounded():
    loaded = []

    def load(rows):
        if rows[0]["venue"] == "kalshi":
            raise ConnectionError("ch down")
        loaded.append(rows)

    buf, _, recorded = run({"kalshi": kalshi_ok, "polymarket": kalshi_ok}, load, max_buffer=1)
    assert [(r.check, r.target) for r in recorded if not r.passed] == [("load", "venue_arb.book_levels:kalshi")]
    assert "ConnectionError" in next(r for r in recorded if r.check == "load").detail
    assert len(loaded) == 1 and len(buf["kalshi"]) == 1 and buf["polymarket"] == []
