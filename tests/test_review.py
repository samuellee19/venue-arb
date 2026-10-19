from collections import Counter

from venue_arb.matching.review import BANDS, precision, recall, stratified_sample


def rows():
    out = []
    for cat, conf, n in (("fed_macro", 0.97, 500), ("crypto_price", 0.85, 20), ("crypto_price", 0.5, 3)):
        out += [{"a_id": f"{cat}{conf}-{i}", "category": cat, "confidence": conf} for i in range(n)]
    return out


def cell(r):
    return r["category"], next(i for i, (lo, hi) in enumerate(BANDS) if lo <= r["confidence"] < hi)


def test_stratified_sample_min_per_cell():
    sample = stratified_sample(rows(), n=50)
    assert len(sample) == 50
    counts = Counter(cell(r) for r in sample)
    for c, size in Counter(cell(r) for r in rows()).items():
        assert counts[c] >= min(5, size)


def test_stratified_sample_deterministic():
    ids = lambda s: [r["a_id"] for r in s]
    assert ids(stratified_sample(rows(), n=50, seed=3)) == ids(stratified_sample(rows(), n=50, seed=3))


def test_precision_excludes_unsure():
    assert precision(["correct"] * 19 + ["wrong"] + ["unsure"] * 5) == (0.95, 20)


def test_recall():
    known = [("k1", "p1"), ("k2", "p2"), ("k3", "p3"), ("k4", "p4")]
    assert recall(known, {("k1", "p1"), ("k2", "p2"), ("x", "y")}) == 0.5
