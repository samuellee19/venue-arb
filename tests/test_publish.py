"""Publisher unit tests: readers injected, no qa / platform stack (as tests/test_collect.py)."""
import os
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pyarrow as pa
import pytest

from tests.test_bundle import (
    BOOKS,
    CTX215,
    INSTANT,
    K_BOOK,
    KPI,
    LABEL_NVDA,
    LADDER,
    LDF,
    NOW,
    PAIR,
    PM_BOOK,
    QUOTE,
    SERIES_FEE,
    SP_NOW,
    SP_SNAP,
    TITLES,
)
from venue_arb import config, publish
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

    assert publish.push(tmp_path, "u@h:./", rsync=rsync) is False
    assert calls == [["rsync", "-az", "--timeout=30", f"{tmp_path}/", "u@h:./"]]
    assert publish.push(tmp_path, "u@h:/x/", rsync=lambda args, **kw: SimpleNamespace(returncode=0)) is True


def _history_fixture():
    t = datetime(2026, 10, 20, 15, 0, 10, tzinfo=UTC)
    books = {("kalshi", "K1"): {t: [Level("no", 0, 0.62, 30.0), Level("yes", 0, 0.41, 120.0)]},
             ("polymarket", "P1"): {t + timedelta(seconds=8): [Level("no", 0, 0.58, 400.0),
                                                                Level("yes", 0, 0.45, 50.0)]},
             ("kalshi", "KA"): {t: [Level("yes", 0, 0.40, 500.0)]},
             ("polymarket", "PA"): {t: [Level("no", 0, 0.45, 300.0)]}}
    edge_rows = [(t, "K1", "P1", "k_yes_pm_no", -0.007, 0.0), (t, "K1", "P1", "k_no_pm_yes", None, 0.0)]
    ladder_rows = [(t, "KA", "PA", "k_yes_pm_no", "near_exact", 0.15, 60.0),
                   (t.replace(tzinfo=None), "KA", "PB", "k_yes_pm_no", "arb", 0.1, 450.0)]
    return t, edge_rows, ladder_rows, books


def test_history_table_schema_and_nulls(monkeypatch):
    monkeypatch.setattr(config, "DASHBOARD_SHOW_BOOKS", True)
    t, edge_rows, ladder_rows, books = _history_fixture()
    tab = publish.history_table(edge_rows, ladder_rows, books, sides={("K1", "P1"): "yes"})
    assert tab.column_names == ["ts", "key", "kind", "direction", "net_edge_cents", "capacity_usd", "k_ask", "k_size",
                                "pm_ask", "pm_size", "fees_cents", "basis_bps", "net_bps"]
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


@pytest.mark.parametrize("show", [True, False])
def test_history_hides_quotes_when_books_hidden(monkeypatch, show):
    monkeypatch.setattr(config, "DASHBOARD_SHOW_BOOKS", show)
    _, edge_rows, ladder_rows, books = _history_fixture()
    rows = publish.history_table(edge_rows, ladder_rows, books, sides={("K1", "P1"): "yes"}).to_pylist()
    quotes = [r[c] for r in rows[:3] for c in ("k_ask", "k_size", "pm_ask", "pm_size")]
    assert all(q is not None for q in quotes) if show else all(q is None for q in quotes)
    assert rows[0]["fees_cents"] == pytest.approx(1.7)  # derived from the asks either way


def _touch_hours(out, hours):
    (out / "history").mkdir(exist_ok=True)
    for h in hours:
        (out / "history" / f"pairs-{h:%Y%m%d%H}.parquet").write_bytes(b"x")


def test_history_due_rewrites_two_hours_and_fills_missing(tmp_path):
    now = datetime(2026, 10, 20, 15, 56, tzinfo=UTC)
    h = publish.history_hour(now)  # 14:00
    window = [h - timedelta(hours=i) for i in range(publish.HISTORY_DAYS * 24)]
    assert publish.history_due(tmp_path, now, None) == sorted(window)  # fresh start: the whole trailing window
    _touch_hours(tmp_path, [x for x in window if x != h - timedelta(hours=30)])
    assert publish.missing_hours(tmp_path, now) == [h - timedelta(hours=30)]
    # the hour moved on: the newest two hours again, plus the one still missing
    assert publish.history_due(tmp_path, now, h - timedelta(hours=1)) == [h - timedelta(hours=30),
                                                                          h - timedelta(hours=1), h]
    assert publish.history_due(tmp_path, now, h) == []


def test_prune_history_keeps_the_server_window(tmp_path):
    now = datetime(2026, 10, 20, 15, 56, tzinfo=UTC)
    edge = now.replace(minute=0) - publish.HISTORY_KEEP
    _touch_hours(tmp_path, [edge - timedelta(hours=1), edge, edge + timedelta(hours=1)])
    gone = publish.prune_history(tmp_path, now)
    assert [p.name for p in gone] == [f"pairs-{edge - timedelta(hours=1):%Y%m%d%H}.parquet"]
    assert sorted(os.listdir(tmp_path / "history")) == [f"pairs-{edge:%Y%m%d%H}.parquet",
                                                        f"pairs-{edge + timedelta(hours=1):%Y%m%d%H}.parquet"]


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
    assert out.kpis.pairs_live == {"exact": 1, "near_exact": 1, "ladder": 3}
    assert out.kpis.admissible_checks == 1200 and out.basis["BTC"].up == 182.0
    assert out.collector_last_snapshot_at == NOW - timedelta(seconds=30)  # newest book read
    # one settlement line per instant, key None; fixture listings carry no first_seen, so no listing lines
    assert [(a.kind, a.at, a.key) for a in out.annotations] == [("settlement", INSTANT, None),
                                                                ("settlement", PAIR.close_time, None)]
    LiveBundle.model_validate_json(out.model_dump_json())


def test_tick_skips_pair_without_listing():
    state = {}
    out = publish.tick(state, readers(drop={("polymarket", PAIR.pm_id)}), NOW)
    assert [p.kind for p in out.pairs].count("exact") == 0 and len(out.pairs) == 5
    assert state["skipped"] == {"pairs_no_listing": 1, "strikes_no_listing": 0, "strikes_no_series": 0,
                                "ladders_no_basis": 0}


def test_tick_skips_strike_without_series_ticker():
    rd, state = readers(), {}
    listings = rd["listings"]
    bad = ("kalshi", "KXBTCD-26OCT2011-T87999.99")

    def no_series(ids):
        out = listings(ids)
        return out | {bad: {**out[bad], "raw": {"event": {"event_ticker": "KXBTCD-26OCT2011"}}}}

    rd["listings"] = no_series
    out = publish.tick(state, rd, NOW)
    assert all(p.legs[0].market_id != bad[1] for p in out.pairs) and len(out.pairs) == 2 + 3  # 85999.99: 2 near-exact + 1
    assert state["skipped"]["strikes_no_series"] == 1


def test_tick_refreshes_every_n():
    calls, state = {}, {}
    rd = readers(calls)
    for i in range(2 * publish.REFRESH_EVERY):
        publish.tick(state, rd, NOW + timedelta(seconds=60 * i))
    assert calls["books"] == 2 * publish.REFRESH_EVERY
    assert calls["pairs"] == calls["ladders"] == calls["listings"] == calls["context"] == 2
    assert calls["series_fee"] == 2 * 2  # two series, fetched once each per refresh


def test_failed_refresh_keeps_previous_state_and_retries():
    calls, state = {}, {}
    rd = readers(calls)
    pairs, fail = rd["pairs"], [True]

    def flaky():
        if fail:
            fail.pop()
            raise ConnectionError("pg down")
        return pairs()

    rd["pairs"] = flaky
    with pytest.raises(ConnectionError):  # nothing to fall back on yet
        publish.tick(state, rd, NOW)
    assert len(publish.tick(state, rd, NOW).pairs) == 7
    fail.append(True)
    state["since_refresh"] = publish.REFRESH_EVERY  # the next refresh is due, and fails
    assert len(publish.tick(state, rd, NOW).pairs) == 7  # previous pairs kept, bundle still built
    assert state["refresh_errors"] == ["ConnectionError"]
    publish.tick(state, rd, NOW)  # retried at once, succeeds
    assert calls["pairs"] == 2 and state["since_refresh"] == 1


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


def test_episodes_sql_window_and_budget():
    assert "coalesce(end_ts, now())" in publish.EPISODES_SQL and "source" in publish.EPISODES_SQL
    assert publish.LIVE_BYTES_BUDGET == 1_200_000


def test_episodes_7d_counts_open_episode():
    row = {"source": "pair", "kalshi_id": "K1", "pm_id": "P1", "direction": "k_yes_pm_no", "start_ts": NOW,
           "end_ts": None, "max_edge": 0.02, "median_capacity_usd": 300.0, "transfer_cost_usd": 5.0}
    rows = [row, {**row, "pm_id": "P2"}, {**row, "pm_id": "P3"}, {**row, "source": "ladder", "kalshi_id": "KX9"}]
    scope = {("K1", "P1"): {"exact": True, "bad_review": False}, ("K1", "P2"): {"exact": True, "bad_review": True}}
    kept = publish.scope_episodes(rows, scope)
    assert [(r["source"], r["kalshi_id"], r["pm_id"]) for r in kept] == [("pair", "K1", "P1"), ("ladder", "KX9", "P1")]


# a stand-in for qa.checks (unit tests run without the platform): Result keeps what _results passes
CHECKS = SimpleNamespace(Result=lambda check, passed, observed=None, threshold=None, detail="", **kw: SimpleNamespace(
    check=check, passed=passed, observed=observed, threshold=threshold, detail=detail, **kw))


def research_readers(calls: list, fail=None, slow=None):
    from tests.test_note import edge, episode, pair, screened

    def inputs():
        calls.append("research")
        if slow:
            slow()
        if fail:
            raise ConnectionError("pg down")
        return {"pairs": [pair()], "screened_rows": [screened()], "edge_rows": [edge()], "episodes": [episode()]}

    return readers() | {"research_inputs": inputs, "review_labels": lambda: (["correct", "correct", "unsure"], NOW)}


def _cycle(state, rd, tmp_path, now, calls, clock=None):
    return publish.cycle(state, rd, tmp_path, "u@h:./", now, checks=CHECKS,
                         write=lambda path, data: calls.append(("write", path.name, data)),
                         push=lambda src, dest: calls.append("push") or True,
                         **({"clock": clock} if clock else {}))


def test_research_runs_after_push_on_hour_change(tmp_path):
    calls, state = [], {"history_hour": publish.history_hour(NOW)}  # no history due: these tests have no db
    rd = research_readers(calls)
    assert _cycle(state, rd, tmp_path, NOW, calls) == []
    assert [c if isinstance(c, str) else c[:2] for c in calls] == [("write", "live.json"), "push", "research"]
    assert LiveBundle.model_validate_json(calls[0][2]).research is None  # computed after this tick's push
    r = state["research"]
    assert r.computed_at == NOW and r.snapshots == 1 and r.review == {"precision": 1.0, "labels": 2, "asof": NOW}
    assert state["research_hour"] == publish.history_hour(NOW)
    calls.clear()
    _cycle(state, rd, tmp_path, NOW + timedelta(minutes=1), calls)  # same hour: no recompute; the bundle carries it
    assert "research" not in calls  # compare as JSON: review's datetime comes back as a string (test_research)
    assert LiveBundle.model_validate_json(calls[0][2]).research.model_dump_json() == r.model_dump_json()
    calls.clear()
    later = NOW + timedelta(hours=1)
    state["history_hour"] = publish.history_hour(later)
    _cycle(state, rd, tmp_path, later, calls)
    assert calls[-1] == "research" and state["research"].computed_at == later


def test_research_failure_keeps_previous(tmp_path):
    calls, state = [], {"history_hour": publish.history_hour(NOW)}
    _cycle(state, research_readers(calls), tmp_path, NOW, calls)
    good = state["research"]
    later = NOW + timedelta(hours=1)
    state["history_hour"] = publish.history_hour(later)
    assert _cycle(state, research_readers(calls, fail=True), tmp_path, later, calls) == []
    # previous summary kept; the hour advances anyway, so the failure is retried next hour, not next cycle
    assert state["research"] is good and state["research_hour"] == publish.history_hour(later)
    calls.clear()
    _cycle(state, research_readers(calls, fail=True), tmp_path, later + timedelta(minutes=1), calls)
    assert "research" not in calls
    del state["live"]  # bundle checks need qa.checks' expect_* helpers; only the research results matter here
    results = {r.check: r for r in publish._results(CHECKS, state, 60)}
    res = results["research_failed"]
    assert (res.passed, res.observed, res.severity) == (False, 1, "warn")
    assert res.detail == "1 research computations failed, previous summary kept"
    assert "research_seconds" in results  # a failed run is a run: its seconds are recorded
    assert "research_seconds" not in {r.check for r in publish._results(CHECKS, state, 60)}  # none ran since
    [ok] = [r for r in publish._results(CHECKS, state, 60) if r.check == "research_failed"]
    assert ok.passed and ok.observed == 0  # counted since the last record only


def test_research_seconds_warn(tmp_path):
    t = [0.0]
    calls, state = [], {"history_hour": publish.history_hour(NOW)}
    rd = research_readers(calls, slow=lambda: t.__setitem__(0, t[0] + publish.RESEARCH_TIMEOUT_WARN_S + 1))
    _cycle(state, rd, tmp_path, NOW, calls, clock=lambda: t[0])
    del state["live"]
    [res] = [r for r in publish._results(CHECKS, state, 60) if r.check == "research_seconds"]
    assert (res.passed, res.observed, res.severity) == (False, publish.RESEARCH_TIMEOUT_WARN_S + 1, "warn")
    assert publish.RESEARCH_TIMEOUT_WARN_S == 30


def test_research_bytes_warn(tmp_path, monkeypatch):
    assert publish.RESEARCH_BYTES_WARN == 150_000
    calls, state = [], {"history_hour": publish.history_hour(NOW)}
    _cycle(state, research_readers(calls), tmp_path, NOW, calls)
    size = len(state["research"].model_dump_json())
    assert state["research_bytes"] == size
    del state["live"]
    monkeypatch.setattr(publish, "RESEARCH_BYTES_WARN", size - 1)
    [res] = [r for r in publish._results(CHECKS, state, 60) if r.check == "research_bytes"]
    assert (res.passed, res.observed, res.severity) == (False, size, "warn")
    assert "research_bytes" not in {r.check for r in publish._results(CHECKS, state, 60)}  # none computed since
    calls.clear()
    later = NOW + timedelta(hours=1)
    state["history_hour"] = publish.history_hour(later)
    _cycle(state, research_readers(calls, fail=True), tmp_path, later, calls)
    assert "research_bytes" not in state  # a failed computation measures nothing


def _perps_readers(calls):
    rd = readers()
    rd["perps"] = lambda: calls.append("perps") or [(CTX215, LABEL_NVDA, SP_SNAP, QUOTE)]
    return rd


def test_tick_skips_stock_perps_while_flag_off(monkeypatch):
    monkeypatch.setattr(config, "DASHBOARD_SHOW_STOCK_PERPS", False)
    calls = []
    out = publish.tick({}, _perps_readers(calls), NOW)
    assert calls == [] and len(out.pairs) == 2 + 5 and out.kpis.stock_perps is None


def test_tick_adds_stock_perp_rows_when_flag_on(monkeypatch):
    monkeypatch.setattr(config, "DASHBOARD_SHOW_STOCK_PERPS", True)
    calls = []
    out = publish.tick({}, _perps_readers(calls), SP_NOW)
    assert calls == ["perps"] and [p.category for p in out.pairs].count("stock_perps") == 1
    assert out.kpis.stock_perps == {"names_live": 1, "episodes_7d": 0}
    LiveBundle.model_validate_json(out.model_dump_json())


def test_tick_survives_a_failing_perps_reader(monkeypatch):
    monkeypatch.setattr(config, "DASHBOARD_SHOW_STOCK_PERPS", True)
    rd = readers()
    rd["perps"] = lambda: 1 / 0
    out = publish.tick({}, rd, NOW)
    assert len(out.pairs) == 2 + 5 and out.kpis.stock_perps == {"names_live": 0, "episodes_7d": 0}


def test_episodes_sql_and_scope_exclude_stock_perp():
    assert "source <> 'stock_perp'" in publish.EPISODES_SQL
    assert "source = 'stock_perp'" in publish.STOCK_PERP_EPISODES_SQL and "7 days" in publish.STOCK_PERP_EPISODES_SQL
    assert publish.scope_episodes([{"source": "stock_perp", "kalshi_id": "x", "pm_id": "y"}], {}) == []


def test_spark_series_maps_stock_perp_rows_to_the_bundle_key():
    start = datetime(2026, 10, 19, 16, tzinfo=UTC)
    rows = [("xyz:NVDA", "", "short_perp_long_stock", start, 0.008432)]
    s = publish.spark_series(rows, start, refs={"xyz:NVDA": "NVDA"})["xyz:NVDA~NVDA~short_perp_long_stock"]
    assert s[0] == pytest.approx(0.8432)  # SPARKS_SQL emits net_bps / 1e4; x100 -> net_bps / 100 (¢ of the net)
    assert "stock_perp_edges" in publish.SPARKS_SQL and "max(net_bps)" in publish.SPARKS_SQL


def test_history_table_has_basis_columns_null_for_pairs():
    t, edge_rows, ladder_rows, books = _history_fixture()
    stock = [(t, "xyz:NVDA", "short_perp_long_stock", 101.2, 84.3, 63864.0)]
    tab = publish.history_table(edge_rows, ladder_rows, books, sides={("K1", "P1"): "yes"}, stock_rows=stock,
                                refs={"xyz:NVDA": "NVDA"})
    rows = tab.to_pylist()
    assert tab.schema.field("basis_bps").type == pa.float64() and tab.schema.field("net_bps").type == pa.float64()
    assert all(r["basis_bps"] is None and r["net_bps"] is None for r in rows[:-1])
    sp = rows[-1]
    assert (sp["key"], sp["kind"], sp["direction"]) == ("xyz:NVDA~NVDA~short_perp_long_stock", "stock_perp",
                                                        "short_perp_long_stock")
    assert (sp["basis_bps"], sp["net_bps"], sp["capacity_usd"]) == (101.2, 84.3, 63864.0)
    assert sp["net_edge_cents"] == pytest.approx(0.843) and sp["k_ask"] is None and sp["fees_cents"] is None


def test_write_history_hour_reads_stock_perp_edges_only_while_flag_on(tmp_path, monkeypatch):
    import pyarrow.parquet as pq

    hour = datetime(2026, 10, 20, 15, tzinfo=UTC)
    t = hour + timedelta(seconds=10)
    results = {"venue_arb.edges": [], "venue_arb.ladder_edges": [], "venue_arb.book_levels_v2": [],
               "venue_arb.stock_perp_edges": [(t, "xyz:NVDA", "short_perp_long_stock", 101.2, 84.3, 63864.0)]}
    ch = SimpleNamespace(query=lambda sql, parameters=None: SimpleNamespace(
        result_rows=next(v for k, v in results.items() if f"FROM {k} " in sql)))
    monkeypatch.setattr(config, "DASHBOARD_SHOW_STOCK_PERPS", False)
    assert pq.read_table(publish.write_history_hour(ch, tmp_path, hour)).num_rows == 0
    monkeypatch.setattr(config, "DASHBOARD_SHOW_STOCK_PERPS", True)
    [row] = pq.read_table(publish.write_history_hour(ch, tmp_path, hour)).to_pylist()
    assert row["key"] == "xyz:NVDA~NVDA~short_perp_long_stock" and row["net_bps"] == 84.3  # labels/stock_perps.csv


def _stock_inputs():
    from tests.test_research import sp_inputs

    return sp_inputs()


def test_compute_research_attaches_stock_block_when_flag_on(monkeypatch):
    calls = []
    rd = research_readers(calls) | {"stock_perp_inputs": lambda: calls.append("stock") or _stock_inputs()}
    monkeypatch.setattr(config, "DASHBOARD_SHOW_STOCK_PERPS", False)
    assert publish.compute_research(rd, NOW).stock_perps is None and "stock" not in calls
    monkeypatch.setattr(config, "DASHBOARD_SHOW_STOCK_PERPS", True)
    r = publish.compute_research(rd, NOW)
    assert r.stock_perps.max_net_bps == 12.4 and calls[-1] == "stock"


def test_stock_perp_reader_failure_keeps_previous_summary(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "DASHBOARD_SHOW_STOCK_PERPS", True)
    calls, state = [], {"history_hour": publish.history_hour(NOW)}
    rd = research_readers(calls) | {"stock_perp_inputs": lambda: _stock_inputs()}
    _cycle(state, rd, tmp_path, NOW, calls)
    good = state["research"]
    assert good.stock_perps is not None
    later = NOW + timedelta(hours=1)
    state["history_hour"] = publish.history_hour(later)
    _cycle(state, research_readers(calls) | {"stock_perp_inputs": lambda: 1 / 0}, tmp_path, later, calls)
    assert state["research"] is good and state["research_failed"] == 1


def _sp_row(minute, net=20.0, funding=0.0, borrow=0.0, d="long_perp_short_stock", sym="xyz:NVDA", cap=500.0):
    return {"ts": NOW + timedelta(minutes=minute), "symbol": sym, "direction": d, "net_bps": net,
            "funding_bps": funding, "borrow_bps": borrow, "capacity_contracts": 5.0, "capacity_usd": cap}


def test_sensitivity_episodes_rescore_hold_and_borrow():
    ticks = lambda **kw: [_sp_row(m, **kw) for m in range(3)]
    # funding received (-20 bps over 24 h) is worth less over 1 h and more over 7 d; borrow x10 costs 9 x 2 = 18 bps
    got = publish.sensitivity_episodes(ticks(net=20.0, funding=-20.0, borrow=2.0))
    assert got == {"episodes_1h": 0, "episodes_7d": 1, "episodes_borrow_x10": 0}
    # a paid-funding run (+12) dies at 7 d; an interleaved other-direction tick splits a run (two runs of one < 2)
    assert publish.sensitivity_episodes(ticks(net=30.0, funding=12.0)) == {
        "episodes_1h": 1, "episodes_7d": 0, "episodes_borrow_x10": 1}
    split = [_sp_row(0), _sp_row(1, d="short_perp_long_stock"), _sp_row(2)]
    assert publish.sensitivity_episodes(split) == {"episodes_1h": 0, "episodes_7d": 0, "episodes_borrow_x10": 0}
    # a thin stock leg never qualifies, whatever the carry
    assert publish.sensitivity_episodes(ticks(cap=50.0))["episodes_1h"] == 0


def test_stock_perp_inputs_assembles_the_block_inputs():
    from datetime import date

    from venue_arb.sources.hyperliquid import Label

    rows = {publish.SP_COUNTS_SQL: [(1, 3, NOW)], publish.SP_SHARE_SQL: [(0.4, 2)],
            publish.SP_HOURLY_SQL: [(NOW, 25.0)], publish.SP_BY_NAME_SQL: [("xyz:NVDA", 25.0, 2)],
            publish.SP_EDGES_SQL: [tuple(_sp_row(m).values()) for m in range(3)]}
    ch = SimpleNamespace(query=lambda sql: SimpleNamespace(result_rows=rows[sql]))
    cols = ["symbol", "friday", "p_fri", "p_sat", "p_sun", "p_open_pre", "p_open_reg", "ref_kind"]
    wk = ("xyz:NVDA", date(2026, 10, 16), 1.0, 1.0, 1.0, None, 1.0, "schwab")

    class Cur(SimpleNamespace):
        def fetchone(self):
            return (7,)

        def fetchall(self):
            return [wk]

    conn = SimpleNamespace(execute=lambda sql: Cur(description=[SimpleNamespace(name=c) for c in cols]))
    out = publish.stock_perp_inputs(ch, conn, {"xyz:NVDA": Label("xyz:NVDA", "schwab", "NVDA", "NASDAQ", "USD", "NVIDIA")})
    assert out["names"] == 1 and out["snapshots"] == 3 and out["window_start"] == NOW and out["episodes"] == 7
    assert out["share_hours"] == 0.4 and out["hourly"] == [(NOW, 25.0)]
    assert out["by_name"] == [{"symbol": "xyz:NVDA", "name": "NVIDIA", "max_net_bps": 25.0, "hours_ge_10": 2,
                               "ref_kind": "schwab"}]
    assert out["sensitivity"]["episodes_1h"] == 1 and out["weekend_rows"][0]["friday"] == date(2026, 10, 16)
    from venue_arb.report import research

    research.stock_perp_block(out, NOW)  # the reader's output is the reducer's input
