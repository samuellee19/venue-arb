import importlib.util
from pathlib import Path

import pyarrow.parquet as pq

from venue_arb.bundle import LiveBundle

SAMPLE = Path(__file__).parent.parent / "dashboard" / "sample"
HOURS = ("pairs-2026102014.parquet", "pairs-2026102015.parquet")
COLS = ["ts", "key", "kind", "direction", "net_edge_cents", "capacity_usd", "k_ask", "k_size", "pm_ask", "pm_size",
        "fees_cents"]
LABELS = ("Exact match · no edge", "Executable edge · +", "Not an arbitrage · basis exposure",
          "Spread · within basis bound · no edge", "No asks on one side", "Stale · a leg has no snapshot in 5 min")


def _bundle():
    return LiveBundle.model_validate_json((SAMPLE / "live.json").read_text())


def test_sample_live_covers_every_ui_path():
    b = _bundle()
    assert len(b.pairs) >= 6 and {p.kind for p in b.pairs} == {"exact", "near_exact", "ladder"}
    for lab in LABELS:
        assert any(p.verdict.label.startswith(lab) for p in b.pairs), lab
    assert len(b.episodes) == 1 and {a.kind for a in b.annotations} == {
        "settlement", "listing", "basis_change", "collector_gap", "threshold"}
    assert not b.show_books and all(p.books is None for p in b.pairs)  # the real (flag-off) config
    assert any(any(x is not None for x in p.spark) for p in b.pairs)


def test_sample_history_has_task4_columns_and_every_pair_key():
    keys = {p.key for p in _bundle().pairs}
    for name in HOURS:
        t = pq.read_table(SAMPLE / "history" / name)
        assert t.column_names == COLS and set(t["key"].to_pylist()) == keys
        assert t.num_rows == 60 * len(keys)
        assert t["k_ask"].null_count == t.num_rows  # quotes hidden while DASHBOARD_SHOW_BOOKS is False
    assert max(pq.read_table(SAMPLE / "history" / HOURS[1])["net_edge_cents"].to_pylist(), key=lambda x: x or 0) > 1


def test_sample_is_reproducible(tmp_path):
    spec = importlib.util.spec_from_file_location("make_sample", SAMPLE / "make_sample.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    mod.build(tmp_path)
    for rel in ("live.json", *(f"history/{n}" for n in HOURS)):
        assert (tmp_path / rel).read_bytes() == (SAMPLE / rel).read_bytes(), rel
