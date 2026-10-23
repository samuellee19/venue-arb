import importlib.util
from datetime import date
from pathlib import Path

import pyarrow.parquet as pq

from venue_arb.bundle import LiveBundle

SAMPLE = Path(__file__).parent.parent / "dashboard" / "sample"
HOURS = ("pairs-2026102014.parquet", "pairs-2026102015.parquet")
COLS = ["ts", "key", "kind", "direction", "net_edge_cents", "capacity_usd", "k_ask", "k_size", "pm_ask", "pm_size",
        "fees_cents", "basis_bps", "net_bps"]
LABELS = ("Exact match · no edge", "Executable edge · +", "Not an arbitrage · basis exposure",
          "Spread · within basis bound · no edge", "No asks on one side", "Stale · a leg has no snapshot in 5 min")


def _bundle():
    return LiveBundle.model_validate_json((SAMPLE / "live.json").read_text(encoding="utf-8"))


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
        for col in ("k_ask", "k_size", "pm_ask", "pm_size"):  # quotes hidden while DASHBOARD_SHOW_BOOKS is False
            assert t[col].null_count == t.num_rows, f"{col} not null: regenerate the sample after flipping DASHBOARD_SHOW_BOOKS"
    assert max(pq.read_table(SAMPLE / "history" / HOURS[1])["net_edge_cents"].to_pylist(), key=lambda x: x or 0) > 1


def test_sample_is_reproducible(tmp_path):
    spec = importlib.util.spec_from_file_location("make_sample", SAMPLE / "make_sample.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    mod.build(tmp_path)
    for rel in ("live.json", *(f"history/{n}" for n in HOURS)):
        assert (tmp_path / rel).read_bytes() == (SAMPLE / rel).read_bytes(), rel


def test_sample_has_research():
    b = _bundle()
    r = b.research
    assert r is not None and 2 <= len(r.headline) <= 3
    assert r.exec_hourly.ts and r.screened_hist.counts and r.ladder_hourly.ts
    assert r.review == {"precision": 1.0, "labels": 31, "asof": "2026-10-19T15:30:00Z"}
    assert {p.category for p in b.pairs} <= set(b.categories)


def _stock():
    return LiveBundle.model_validate_json((SAMPLE / "live-stock.json").read_text(encoding="utf-8"))


def test_sample_stock_covers_three_states():
    b = _stock()
    sp = [p for p in b.pairs if p.kind == "stock_perp"]
    assert len(sp) == 4 and {p.kind for p in b.pairs} >= {"exact", "stock_perp"}
    labels = [p.verdict.label for p in sp]
    assert any(lab.startswith("Executable edge · +") for lab in labels)
    assert "Basis · within costs · no edge" in labels and "Reference closed · oracle only" in labels
    assert "Stale · a leg has no snapshot in 5 min" in labels
    [stale] = [p for p in sp if not p.live]
    assert stale.net_bps is None and stale.basis_bps is None and stale.sensitivity is None and stale.costs is None
    assert all(p.costs for p in sp if p.live) and all(date.fromisoformat(w["friday"]) < date(2026, 10, 20) and date.fromisoformat(w["friday"]).weekday() == 4
                                                      for w in b.research.stock_perps.weekends["rows"])
    assert all(p.category == "stock_perps" and p.settles_at is None and p.legs[1].url is None for p in sp)
    assert all(p.key == f"{p.legs[0].market_id}~{p.legs[1].market_id}~stock_perp" for p in sp)  # no direction in the key
    assert "stock_perps" in b.categories and b.kpis.stock_perps is not None
    assert b.research.stock_perps is not None and b.research.stock_perps.hourly.ts
    assert set(b.research.method) >= {"matching", "edge", "crypto", "not_shown", "stock_perps"}
    assert _bundle().kpis.stock_perps is None and _bundle().research.stock_perps is None  # flag-off sample unchanged
    assert set(_bundle().research.method) == {"matching", "edge", "crypto", "not_shown"}  # no stock-perp prose


def test_sample_stock_is_reproducible(tmp_path):
    spec = importlib.util.spec_from_file_location("make_sample", SAMPLE / "make_sample.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    mod.build_stock(tmp_path)
    assert (tmp_path / "live-stock.json").read_bytes() == (SAMPLE / "live-stock.json").read_bytes()
