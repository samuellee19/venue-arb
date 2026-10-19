from datetime import UTC, datetime, timedelta

import numpy as np

from venue_arb.matching.candidates import embed_pairs, merge, needs_embedding, rule_pairs, unadjudicated

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


def test_needs_embedding_skips_current_versions():
    rows = [{"venue": "kalshi", "market_id": "k", "version": "v1"}, {"venue": "kalshi", "market_id": "k2", "version": "v2"}]
    assert needs_embedding(rows, {("kalshi", "k", "v1")}) == rows[1:]
    assert needs_embedding(rows, {("kalshi", "k", "v0")}) == rows  # old version does not count


def test_unadjudicated_orders_by_score_skips_done_and_limits():
    k = {x: {"market_id": x, "version": "v"} for x in ("k1", "k2", "k3")}
    p = {x: {"market_id": x, "version": "w"} for x in ("p1", "p2", "p3")}
    cands = [("k1", "p1", 0.8, "embed"), ("k2", "p2", 0.99, "rule"), ("k3", "p3", 0.9, "embed"), ("k9", "p1", 1.0, "rule")]
    out = unadjudicated(cands, k, p, {("k3", "v", "p3", "w")}, 2)
    assert [(a["market_id"], b["market_id"]) for a, b in out] == [("k2", "p2"), ("k1", "p1")]
    assert unadjudicated(cands, k, p, {("k2", "v", "p2", "OLD")}, 1)[0][0]["market_id"] == "k2"  # old version redone
