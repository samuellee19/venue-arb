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


def strike(venue, n, level):
    from venue_arb.matching.crypto_rule import Strike
    return Strike(venue, f"{venue[0]}{n}", f"T{n}" if venue == "polymarket" else None, "BTC",
                  datetime(2026, 1, 1, tzinfo=UTC), "gt", float(level))


def test_watchlist_ladder_clip_and_cap():
    from venue_arb.matching.crypto_rule import clip
    pm = [strike("polymarket", i, 100 + i) for i in range(5)]  # 100..104, mid 102
    k = [strike("kalshi", i, 90 + 2 * i) for i in range(20)]  # 90..128; clip pad 10 keeps 90..114
    lad = clip({"polymarket": pm, "kalshi": k}, 10)
    rows = [pair(1, "exact"), pair(2, "near"), pair(3, None, 0.9)]
    wl = watchlist(rows, [lad], cap=100, ladder_cap=8)
    ids = [m for _, m, _ in wl]
    assert ids[:2] == ["K1", "P1"] and ids[10:] == ["K2", "P2", "K3", "P3"]
    ladder = ids[2:10]
    assert len(ladder) == 8 and ("polymarket", "p2", "T2") in wl and ("kalshi", "k6", None) in wl  # 102 is nearest
    assert "k0" not in ids and "k12" not in ids  # clipped (90.. kept only inside 90..114) / far from mid
    assert len(watchlist(rows, [lad], cap=5)) == 5
    assert watchlist(rows, [], cap=100) == watchlist(rows, cap=100)
