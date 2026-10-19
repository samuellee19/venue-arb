from datetime import UTC, datetime

from venue_arb.books import to_rows, watchlist
from venue_arb.models import Level


def pair(n, relation=None, score=0.5):
    return {"a_venue": "kalshi", "a_id": f"K{n}", "a_token": None,
            "b_venue": "polymarket", "b_id": f"P{n}", "b_token": f"T{n}",
            "relation": relation, "score": score}


def test_watchlist_order_and_cap():
    rows = [pair(1, None, 0.9), pair(2, "near"), pair(3, "different"), pair(4, "exact"), pair(5, None, 0.95)]
    wl = watchlist(rows, cap=100)
    assert [m for _, m, _ in wl] == ["K4", "P4", "K2", "P2", "K5", "P5", "K1", "P1"]
    assert ("polymarket", "P4", "T4") in wl and ("kalshi", "K4", None) in wl
    assert len(watchlist(rows, cap=3)) == 3


def test_watchlist_dedupes_shared_legs():
    wl = watchlist([pair(1, "exact"), {**pair(2, "near"), "a_id": "K1"}])
    assert [m for _, m, _ in wl].count("K1") == 1


def test_to_rows_shape():
    ts = datetime(2026, 1, 1, tzinfo=UTC)
    rows = to_rows("kalshi", "K1", ts, [Level("yes", 0, 0.4, 10.0), Level("no", 0, 0.6, 5.0)])
    assert set(rows[0]) == {"ts", "venue", "market_id", "side", "level", "price", "size"}
    assert rows[1]["side"] == "no" and rows[0]["ts"] is ts
