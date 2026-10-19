from datetime import UTC, datetime, timedelta

import numpy as np

from venue_arb.matching.candidates import embed_pairs, merge, rule_pairs

T = datetime(2026, 10, 28, tzinfo=UTC)


def row(mid, category="fed_macro", title="t", event_id="e", close=T):
    return {"market_id": mid, "category": category, "title": title, "event_id": event_id, "close_time": close}


def vecs(n):
    return np.tile(np.array([[1.0, 0.0]]), (n, 1))


def test_embed_pairs_respects_category():
    k, p = [row("k", "fed_macro")], [row("p", "crypto_price")]
    assert embed_pairs(k, p, vecs(1), vecs(1)) == []


def test_embed_pairs_date_window():
    k, p = [row("k")], [row("p", close=T + timedelta(days=40))]
    assert embed_pairs(k, p, vecs(1), vecs(1)) == []
    p = [row("p", close=T + timedelta(days=3))]
    assert embed_pairs(k, p, vecs(1), vecs(1)) == [("k", "p", 1.0)]


def test_rule_pairs_fomc_month():
    k = [row("k", event_id="KXFEDDECISION-26OCT")]
    ok = [row("p", title="Fed decision in October 2026?")]
    assert rule_pairs(k, ok) == [("k", "p", 1.0)]
    assert rule_pairs(k, [row("p", title="Fed decision in December 2026?")]) == []
    # year absent from title: falls back to close_time year
    assert rule_pairs(k, [row("p", title="Fed rate cut in October?")]) == [("k", "p", 1.0)]
    assert rule_pairs(k, [row("p", title="Fed decision in October 2025?")]) == []


def test_merge_prefers_rule():
    out = merge([("k", "p", 1.0)], [("k", "p", 0.8), ("k", "q", 0.9)])
    assert sorted(out) == [("k", "p", 1.0, "rule"), ("k", "q", 0.9, "embed")]
