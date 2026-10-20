from datetime import UTC, datetime

import numpy as np

from venue_arb.bakeoff.evaluate import recall, split, vector_stats

T = datetime(2026, 10, 20, tzinfo=UTC)
TRUTH = [("k1", "p1", "crypto_above"), ("k1", "p2", "crypto_above"), ("k2", "p3", "crypto_above"),
         ("f1", "q1", "fed_exact")]


def test_recall_pairs_and_kalshi_hits_per_group():
    got = recall(TRUTH, {("k1", "p1"), ("f1", "q1"), ("k2", "pX")})
    assert got["crypto_above"] == {"pairs": 3, "found": 1, "recall": 1 / 3, "kalshi": 2, "kalshi_hit": 0.5}
    assert got["fed_exact"]["recall"] == 1.0
    assert got["all"]["found"] == 2 and got["all"]["pairs"] == 4


def test_split_keeps_row_order_per_venue():
    rows = [{"venue": "polymarket", "market_id": "p"}, {"venue": "kalshi", "market_id": "k"}]
    v = np.array([[0.0, 1.0], [1.0, 0.0]], dtype=np.float32)
    (kr, kv), (pr, pv) = split(rows, v)
    assert [r["market_id"] for r in kr] == ["k"] and kv.tolist() == [[1.0, 0.0]]
    assert [r["market_id"] for r in pr] == ["p"] and pv.tolist() == [[0.0, 1.0]]


def test_vector_stats_flags_bad_rows():
    v = np.array([[1.0, 0.0], [0.6, 0.8], [2.0, 0.0], [np.nan, 0.0]], dtype=np.float32)
    s = vector_stats(v)
    assert s == {"rows": 4, "dim": 2, "non_finite": 1, "max_norm_err": 1.0}
