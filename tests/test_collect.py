from datetime import UTC, datetime

from venue_arb.collect import flush, tick
from venue_arb.models import Level

WATCH = [("kalshi", "K1", None), ("polymarket", "P1", "T1")]


def test_collect_one_venue_down():
    def pm_down(token):
        raise TimeoutError("down")

    fetchers = {"kalshi": lambda mid: [Level("yes", 0, 0.4, 10.0)], "polymarket": pm_down}
    buf = {"kalshi": [], "polymarket": []}
    errors = tick(WATCH, fetchers, buf, datetime.now(UTC))
    assert errors == {"kalshi": 0, "polymarket": 1}

    loaded, recorded = [], []
    flush(buf, {"kalshi", "polymarket"}, loaded.append, recorded.extend, list)

    assert [r["venue"] for rows in loaded for r in rows] == ["kalshi"]
    failed = [r for r in recorded if not r.passed]
    assert [(r.check, r.target) for r in failed] == [("expect_rows", "venue_arb.book_levels:polymarket")]
    assert buf == {"kalshi": [], "polymarket": []}
