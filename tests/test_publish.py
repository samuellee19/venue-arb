"""Publisher unit tests: readers injected, no qa / platform stack (as tests/test_collect.py)."""
import os
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pyarrow as pa
import pytest

from tests.test_bundle import BOOKS, INSTANT, K_BOOK, KPI, LADDER, LDF, NOW, PAIR, PM_BOOK, SERIES_FEE, TITLES
from venue_arb import publish
from venue_arb.bundle import LiveBundle
from venue_arb.models import Level


def test_latest_books_picks_newest_ts():
    t0 = datetime(2026, 10, 20, 15, 28, tzinfo=UTC)
    t1 = t0 + timedelta(minutes=1)
    rows = [(t1, "kalshi", "K1", "yes", 0, 0.42, 10.0), (t0, "kalshi", "K1", "yes", 0, 0.40, 5.0),
            (t1, "kalshi", "K1", "no", 0, 0.60, 7.0), (t0.replace(tzinfo=None), "polymarket", "P1", "no", 0, 0.5, 1.0)]
    out = publish.latest_books(rows)
    assert out[("kalshi", "K1")] == (t1, [Level("no", 0, 0.60, 7.0), Level("yes", 0, 0.42, 10.0)])
    assert out[("polymarket", "P1")] == (t0, [Level("no", 0, 0.5, 1.0)])  # naive ClickHouse time read as UTC


def test_write_atomic(tmp_path, monkeypatch):
    path = tmp_path / "live.json"
    path.write_bytes(b"old")

    def crash(src, dst):
        raise OSError("crash between temp write and replace")

    monkeypatch.setattr(os, "replace", crash)
    with pytest.raises(OSError):
        publish.write_atomic(path, b"new")
    assert path.read_bytes() == b"old" and os.listdir(tmp_path) == ["live.json"]
    monkeypatch.undo()
    publish.write_atomic(path, b"new")
    assert path.read_bytes() == b"new" and os.listdir(tmp_path) == ["live.json"]
    assert path.stat().st_mode & 0o777 == 0o644  # readable by the server's app user after rsync -a


def test_push_returns_false_on_nonzero_exit(tmp_path):
    calls = []

    def rsync(args, **kw):
        calls.append(args)
        return SimpleNamespace(returncode=23)

    assert publish.push(tmp_path, "u@h:/srv/venue-arb/bundle/", rsync=rsync) is False
    assert calls == [["rsync", "-az", "--timeout=30", "--partial", f"{tmp_path}/", "u@h:/srv/venue-arb/bundle/"]]
    assert publish.push(tmp_path, "u@h:/x/", rsync=lambda args, **kw: SimpleNamespace(returncode=0)) is True


def test_history_table_schema_and_nulls():
    t = datetime(2026, 10, 20, 15, 0, 10, tzinfo=UTC)
    books = {("kalshi", "K1"): {t: [Level("no", 0, 0.62, 30.0), Level("yes", 0, 0.41, 120.0)]},
             ("polymarket", "P1"): {t + timedelta(seconds=8): [Level("no", 0, 0.58, 400.0),
                                                                Level("yes", 0, 0.45, 50.0)]},
             ("kalshi", "KA"): {t: [Level("yes", 0, 0.40, 500.0)]},
             ("polymarket", "PA"): {t: [Level("no", 0, 0.45, 300.0)]}}
    edge_rows = [(t, "K1", "P1", "k_yes_pm_no", -0.007, 0.0), (t, "K1", "P1", "k_no_pm_yes", None, 0.0)]
    ladder_rows = [(t, "KA", "PA", "k_yes_pm_no", "near_exact", 0.15, 60.0),
                   (t.replace(tzinfo=None), "KA", "PB", "k_yes_pm_no", "arb", 0.1, 450.0)]
    tab = publish.history_table(edge_rows, ladder_rows, books, sides={("K1", "P1"): "yes"})
    assert tab.column_names == ["ts", "key", "kind", "direction", "net_edge_cents", "capacity_usd", "k_ask", "k_size",
                                "pm_ask", "pm_size", "fees_cents"]
    assert tab.schema.field("ts").type == pa.timestamp("ms", tz="UTC")
    rows = tab.to_pylist()
    assert [r["kind"] for r in rows] == ["exact", "exact", "near_exact", "ladder"]
    ex = rows[0]  # K yes 0.41 + PM no 0.58 (nearest PM snapshot, 8 s later), net -0.7¢ -> fees 1.7¢
    assert (ex["key"], ex["k_ask"], ex["k_size"], ex["pm_ask"], ex["pm_size"]) == ("K1~P1~k_yes_pm_no", 0.41, 120.0,
                                                                                   0.58, 400.0)
    assert ex["net_edge_cents"] == pytest.approx(-0.7) and ex["fees_cents"] == pytest.approx(1.7)
    assert rows[1]["k_ask"] == 0.62 and rows[1]["pm_ask"] == 0.45  # k_no_pm_yes legs; no edge -> no fee
    assert rows[1]["net_edge_cents"] is None and rows[1]["fees_cents"] is None
    assert rows[2]["k_ask"] == 0.40 and rows[2]["pm_ask"] == 0.45 and rows[2]["fees_cents"] == pytest.approx(0.0)
    lad = rows[3]
    assert lad["k_ask"] is None and lad["pm_ask"] is None and lad["fees_cents"] is None
    assert lad["net_edge_cents"] == pytest.approx(10.0) and lad["capacity_usd"] == 450.0
    assert lad["ts"] == t  # naive ClickHouse time read as UTC
    # an exact pair without a known yes mapping keeps its edge but not the legs' asks
    unknown = publish.history_table(edge_rows[:1], [], books).to_pylist()[0]
    assert unknown["k_ask"] is None and unknown["net_edge_cents"] == pytest.approx(-0.7)


def _pm_listing(r):  # listings.raw = {"event", "market"}; the ladder fixture's PM fee lives in raw["market"]
    market = {"feeSchedule": LDF["pm_fee"]["schedule"], "feesEnabled": LDF["pm_fee"]["fees_enabled"]}
    return {**r, "raw": {**r["raw"], "market": market}} if r["venue"] == "polymarket" else r


def readers(calls=None, drop=()):
    calls = {} if calls is None else calls

    def counted(name, fn):
        def wrapped(*a):
            calls[name] = calls.get(name, 0) + 1
            return fn(*a)
        return wrapped

    listings = {k: _pm_listing(v) for k, v in TITLES.items() if k not in drop}
    fees = {"KXFED": SERIES_FEE, "KXBTCD": LDF["series_fee"]}
    books = BOOKS | {("kalshi", PAIR.kalshi_id): K_BOOK, ("polymarket", PAIR.pm_id): PM_BOOK}
    return {
        "pairs": counted("pairs", lambda: [PAIR]),
        "ladders": counted("ladders", lambda now: ({("BTC", INSTANT): LADDER}, {"BTC": (182.0, 152.0)})),
        "listings": counted("listings", lambda ids: {k: v for k, v in listings.items() if k in ids}),
        "series_fee": counted("series_fee", fees.__getitem__),
        "books": counted("books", lambda k, p: {key: b for key, b in books.items()
                                                if key[1] in (k if key[0] == "kalshi" else p)}),
        "context": counted("context", lambda now: {"sparks": {}, "episodes": [], "annotations": [],
                                                   "kpi_inputs": KPI}),
    }


def test_tick_composes_bundle_with_injected_readers():
    out = publish.tick({}, readers(), NOW)
    assert isinstance(out, LiveBundle)
    assert len(out.pairs) == 2 + 5  # exact: both directions; ladder: 2 near-exact + 3 ladder rows (test_bundle)
    assert out.kpis.pairs_live == {"exact": 2, "near_exact": 2, "ladder": 3}
    assert out.kpis.admissible_checks == 1200 and out.basis["BTC"].up == 182.0
    assert out.collector_last_snapshot_at == NOW - timedelta(seconds=30)  # newest book read
    assert {a.kind for a in out.annotations} == {"settlement"}  # fixture listings carry no first_seen
    assert {a.key for a in out.annotations} == {p.key for p in out.pairs}
    LiveBundle.model_validate_json(out.model_dump_json())


def test_tick_skips_pair_without_listing():
    state = {}
    out = publish.tick(state, readers(drop={("polymarket", PAIR.pm_id)}), NOW)
    assert [p.kind for p in out.pairs].count("exact") == 0 and len(out.pairs) == 5
    assert state["skipped"] == {"pairs_no_listing": 1, "strikes_no_listing": 0, "ladders_no_basis": 0}


def test_tick_refreshes_every_n():
    calls, state = {}, {}
    rd = readers(calls)
    for i in range(2 * publish.REFRESH_EVERY):
        publish.tick(state, rd, NOW + timedelta(seconds=60 * i))
    assert calls["books"] == 2 * publish.REFRESH_EVERY
    assert calls["pairs"] == calls["ladders"] == calls["listings"] == calls["context"] == 2
    assert calls["series_fee"] == 2 * 2  # two series, fetched once each per refresh


def test_failed_refresh_is_retried_next_tick():
    rd, state, fail = readers(), {}, [True]
    pairs = rd["pairs"]

    def flaky():
        if fail:
            fail.pop()
            raise ConnectionError("pg down")
        return pairs()

    rd["pairs"] = flaky
    with pytest.raises(ConnectionError):
        publish.tick(state, rd, NOW)
    assert len(publish.tick(state, rd, NOW).pairs) == 7


def test_context_rows_to_sparks_and_gaps():
    start = datetime(2026, 10, 19, 16, tzinfo=UTC)
    rows = [("K1", "P1", "k_yes_pm_no", start, 0.012), ("K1", "P1", "k_yes_pm_no", start + timedelta(hours=23), None),
            ("K1", "P1", "k_yes_pm_no", (start + timedelta(hours=5)).replace(tzinfo=None), -0.004)]
    s = publish.spark_series(rows, start)["K1~P1~k_yes_pm_no"]
    assert len(s) == 24 and s[0] == pytest.approx(1.2) and s[5] == pytest.approx(-0.4) and s[23] is None
    m = [start + timedelta(minutes=i) for i in (0, 1, 2, 6, 7)]
    [gap] = publish.gap_annotations(m)
    assert (gap.kind, gap.at, gap.until, gap.key) == ("collector_gap", m[2], m[3], None)


def test_history_hour_waits_for_the_scoring_flows():
    assert publish.history_hour(datetime(2026, 10, 20, 15, 54, tzinfo=UTC)) == datetime(2026, 10, 20, 13, tzinfo=UTC)
    assert publish.history_hour(datetime(2026, 10, 20, 15, 55, tzinfo=UTC)) == datetime(2026, 10, 20, 14, tzinfo=UTC)


def test_write_history_hour_writes_zstd_parquet(tmp_path):
    import pyarrow.parquet as pq

    hour = datetime(2026, 10, 20, 15, tzinfo=UTC)
    t = hour + timedelta(seconds=10)
    results = {"venue_arb.edges": [(t, "K1", "P1", "k_yes_pm_no", 0.02, 300.0)], "venue_arb.ladder_edges": [],
               "venue_arb.book_levels_v2": [(t, "kalshi", "K1", "yes", 0, 0.4, 10.0),
                                            (t, "polymarket", "P1", "no", 0, 0.5, 20.0)]}
    ch = SimpleNamespace(query=lambda sql, parameters=None: SimpleNamespace(
        result_rows=next(v for k, v in results.items() if f"FROM {k} " in sql)))
    path = publish.write_history_hour(ch, tmp_path, hour, sides={("K1", "P1"): "yes"})
    assert path == tmp_path / "history" / "pairs-2026102015.parquet"
    assert pq.ParquetFile(path).metadata.row_group(0).column(0).compression == "ZSTD"
    assert pq.read_table(path).to_pylist()[0]["fees_cents"] == pytest.approx(100 * (1 - 0.4 - 0.5 - 0.02))
    assert sorted(os.listdir(path.parent)) == ["pairs-2026102015.parquet"]
    assert isinstance(path, Path)
