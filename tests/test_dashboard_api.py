import json
import os
import shutil
import subprocess
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import quote

import pytest
from fastapi.testclient import TestClient

from dashboard.api import app as app_mod
from dashboard.api.store import Store
from venue_arb.config import REPO_URL
from venue_arb.edges import fill
from venue_arb.fees import kalshi_taker_fee, polymarket_taker_fee, transfer_cost
from venue_arb.models import Level

ROOT = Path(__file__).parent.parent
SAMPLE = ROOT / "dashboard" / "sample"
LADDER_KEY = "KXBTCD-26OCT2011-T85999.99~0x188be2dc41a51a99434bd3c92c448aa20849d30f27de27b33747423afd6d10d1~k_yes_pm_no"
CPI_KEY = "KXCPI-26NOV-T0.3~0x4e1d028cf5b590152b1271abea39731ff4448ee059e0501be6f208c6f7f868dc~k_yes_pm_no"
K_BOOK = [{"price": 0.41, "size": 300.0}, {"price": 0.43, "size": 500.0}]
PM_BOOK = [{"price": 0.52, "size": 200.0}, {"price": 0.55, "size": 1000.0}]


def _client(monkeypatch, bundle_dir: Path) -> TestClient:
    monkeypatch.setattr(app_mod, "store", Store(bundle_dir))
    return TestClient(app_mod.app)


@pytest.fixture
def client(monkeypatch):
    return _client(monkeypatch, SAMPLE)


def _p(key: str) -> str:
    return quote(key, safe="")


def test_live_and_health(client):
    r = client.get("/api/live")
    assert r.status_code == 200
    body = r.json()
    assert body["bundle"]["computed_at"] == "2026-10-20T15:30:00Z" and len(body["bundle"]["pairs"]) == 11
    assert isinstance(body["age_s"], float)
    h = client.get("/healthz").json()
    assert h["ok"] is True and h["pairs"] == 11 and h["age_s"] >= body["age_s"]
    assert client.get("/api/live", headers={"Accept-Encoding": "gzip"}).headers["content-encoding"] == "gzip"
    page = client.get("/")
    assert page.status_code == 200
    assert REPO_URL in page.text and "__REPO_URL__" not in page.text
    for u in ("/docs", "/redoc", "/openapi.json"):
        assert client.get(u).status_code == 404, u  # no schema or docs page on the public server


def test_no_bundle_yet_is_503(monkeypatch, tmp_path):
    c = _client(monkeypatch, tmp_path)
    for path in ("/api/live", "/healthz"):
        r = c.get(path)
        assert r.status_code == 503 and r.json() == {"detail": "no bundle yet"}


def test_history_ranges(client):
    cols = {"ts", "net_edge_cents", "capacity_usd", "k_ask", "k_size", "pm_ask", "pm_size", "fees_cents", "basis_bps",
            "net_bps"}  # the last two are null in files written before they existed
    day = client.get(f"/api/pair/{_p(CPI_KEY)}/history", params={"range": "24h"}).json()
    assert set(day) == cols and len({len(v) for v in day.values()}) == 1
    assert 0 < len(day["ts"]) <= 1440 and len(day["ts"]) == 120 and day["ts"] == sorted(day["ts"])
    hour = client.get(f"/api/pair/{_p(CPI_KEY)}/history", params={"range": "1h"}).json()
    assert len(hour["ts"]) == 61 and hour["ts"][-1] == "2026-10-20T15:59:00Z"  # newest row minus 1 h, inclusive
    every = client.get(f"/api/pair/{_p(CPI_KEY)}/history", params={"range": "all"}).json()
    assert every["ts"] == ["2026-10-20T14:00:00Z", "2026-10-20T15:00:00Z"]  # hourly
    store = Store(SAMPLE)
    raw = store.history(CPI_KEY, "24h")
    assert every["net_edge_cents"][0] == pytest.approx(
        sum(x for t, x in zip(raw["ts"], raw["net_edge_cents"]) if t < "2026-10-20T15" and x is not None)
        / sum(1 for t, x in zip(raw["ts"], raw["net_edge_cents"]) if t < "2026-10-20T15" and x is not None))
    assert client.get(f"/api/pair/{_p(CPI_KEY)}/history", params={"range": "2d"}).status_code == 422


def test_history_without_history_dir_is_empty(tmp_path):
    shutil.copy(SAMPLE / "live.json", tmp_path / "live.json")
    assert Store(tmp_path).history(CPI_KEY, "all") == {}


def test_corrupt_history_file_is_empty_not_500(monkeypatch, tmp_path):
    shutil.copytree(SAMPLE, tmp_path, dirs_exist_ok=True)
    next((tmp_path / "history").glob("pairs-*.parquet")).write_bytes(b"not a parquet file")
    c = _client(monkeypatch, tmp_path)
    r = c.get(f"/api/pair/{_p(CPI_KEY)}/history?range=24h")
    assert r.status_code == 200 and r.json() == {}
    assert c.get(f"/api/pair/{_p(CPI_KEY)}/snapshot?ts=2026-10-20T15:00:00Z").status_code == 404


def test_snapshot_nearest(client):
    r = client.get(f"/api/pair/{_p(CPI_KEY)}/snapshot", params={"ts": "2026-10-20T15:10:40Z"})
    assert r.status_code == 200 and r.json()["ts"] == "2026-10-20T15:11:00Z"
    assert set(r.json()) >= {"ts", "net_edge_cents", "capacity_usd", "fees_cents"}
    s = Store(SAMPLE)
    assert s.snapshot(CPI_KEY, datetime(2026, 10, 20, 15, 10, 10, tzinfo=UTC))["ts"] == "2026-10-20T15:10:00Z"
    assert s.snapshot(CPI_KEY, datetime(2026, 10, 20, 16, 0, 31, tzinfo=UTC)) is None  # > 90 s past the last row
    assert s.snapshot(CPI_KEY, datetime(2026, 10, 20, 16, 0, 29, tzinfo=UTC))["ts"] == "2026-10-20T15:59:00Z"
    far = client.get(f"/api/pair/{_p(CPI_KEY)}/snapshot", params={"ts": "2026-10-21T00:00:00Z"})
    assert far.status_code == 404


def _books_copy(tmp_path: Path) -> Path:
    d = tmp_path / "bundle"
    shutil.copytree(SAMPLE, d)
    b = json.loads((d / "live.json").read_text(encoding="utf-8"))
    b["show_books"] = True
    for p in b["pairs"]:
        if p["key"] == LADDER_KEY:
            p["books"] = [K_BOOK, PM_BOOK]
    (d / "live.json").write_text(json.dumps(b), encoding="utf-8")
    return d


def test_walk_matches_fill(monkeypatch, tmp_path):
    c = _client(monkeypatch, _books_copy(tmp_path))
    r = c.get("/api/walk", params={"key": LADDER_KEY, "size": 400})
    assert r.status_code == 200
    # the ladder pair's fee_params in the sample: Kalshi quadratic x1, Polymarket enabled 0.07 exponent 1
    expect = fill([Level("yes", i, **lv) for i, lv in enumerate(K_BOOK)],
                  [Level("no", i, **lv) for i, lv in enumerate(PM_BOOK)],
                  lambda p, q: kalshi_taker_fee(p, q, "quadratic", 1),
                  lambda p, q: polymarket_taker_fee(p, q, {"rate": 0.07, "exponent": 1}, True), 400)
    body = r.json()
    for f in ("requested", "filled", "insufficient_depth", "k_avg_price", "pm_avg_price", "fees_usd", "cost_usd",
              "net_cents", "net_bps"):
        assert body[f] == pytest.approx(getattr(expect, f)), f
    assert body["transfer_cost_usd"] == pytest.approx(transfer_cost(expect.cost_usd))
    deep = c.get("/api/walk", params={"key": LADDER_KEY, "size": 5000}).json()
    assert deep["insufficient_depth"] is True and deep["filled"] == 800  # Kalshi depth 300 + 500
    for bad in (0, -1, 1e6 + 1):
        assert c.get("/api/walk", params={"key": LADDER_KEY, "size": bad}).status_code == 422
    assert c.get("/api/walk", params={"key": CPI_KEY, "size": 10}).status_code == 409  # this pair has no books
    assert c.get("/api/walk", params={"key": "nope", "size": 10}).status_code == 404


def test_walk_409_while_books_hidden(client):
    r = client.get("/api/walk", params={"key": LADDER_KEY, "size": 10})
    assert r.status_code == 409 and r.json() == {"detail": "books not published"}


def test_pair_key_roundtrip_and_404(client):
    enc = _p(LADDER_KEY)
    assert "~" in LADDER_KEY and "." in LADDER_KEY and "-" in LADDER_KEY and "0x" in LADDER_KEY
    r = client.get(f"/api/pair/{enc}/history", params={"range": "1h"})
    assert r.status_code == 200 and len(r.json()["ts"]) == 61
    made_up = _p("KXNOPE-26OCT20-T1~0xdead~k_yes_pm_no")
    assert client.get(f"/api/pair/{made_up}/history", params={"range": "1h"}).status_code == 404
    assert client.get(f"/api/pair/{made_up}/snapshot", params={"ts": "2026-10-20T15:10:00Z"}).status_code == 404


def test_store_keeps_last_good_bundle(tmp_path):
    shutil.copy(SAMPLE / "live.json", tmp_path / "live.json")
    s = Store(tmp_path)
    good, age1 = s.live()
    (tmp_path / "live.json").write_text('{"half":', encoding="utf-8")
    time.sleep(0.01)
    again, age2 = s.live()
    assert again is good and age2 > age1


def test_server_imports_without_pipeline_deps():
    shim = ("import sys\n"
            "for m in ('prefect', 'anthropic', 'fastembed', 'clickhouse_connect', 'psycopg', 'qa'):\n"
            "    sys.modules[m] = None\n"
            "import venue_arb.edges, venue_arb.bundle, dashboard.api.app\n")
    r = subprocess.run([sys.executable, "-c", shim], cwd=ROOT, capture_output=True, text=True, check=False)
    assert r.returncode == 0, r.stderr


def test_ranged_reads_open_only_the_hours_they_need(monkeypatch, tmp_path):
    # an unreadable hour file far outside the range must not matter to 1h/24h or a snapshot: they read only the hour
    # files their window covers (the VM holds ~340 hour files; a scan of all of them per request took 18-25 s)
    shutil.copytree(SAMPLE, tmp_path, dirs_exist_ok=True)
    (tmp_path / "history" / "pairs-2026100100.parquet").write_bytes(b"not a parquet file")
    c = _client(monkeypatch, tmp_path)
    day = c.get(f"/api/pair/{_p(CPI_KEY)}/history", params={"range": "24h"}).json()
    assert len(day["ts"]) == 120
    assert len(c.get(f"/api/pair/{_p(CPI_KEY)}/history", params={"range": "1h"}).json()["ts"]) == 61
    assert c.get(f"/api/pair/{_p(CPI_KEY)}/snapshot", params={"ts": "2026-10-20T15:10:40Z"}).status_code == 200
    # "all" still reads every hour, so the bad file empties it as before
    assert c.get(f"/api/pair/{_p(CPI_KEY)}/history", params={"range": "all"}).json() == {}


def test_duckdb_is_bounded(tmp_path):
    con = Store(tmp_path)._con
    assert con.execute("SELECT current_setting('threads')").fetchone()[0] == 1
    limit = con.execute("SELECT current_setting('memory_limit')").fetchone()[0]
    assert limit.endswith("MiB") and float(limit.split()[0]) <= 256  # the VM has 1 GB in all


def test_live_serialises_each_bundle_once(monkeypatch, client):
    from venue_arb.bundle import LiveBundle

    calls = []
    real = LiveBundle.model_dump_json
    monkeypatch.setattr(LiveBundle, "model_dump_json", lambda self, **kw: calls.append(1) or real(self, **kw))
    monkeypatch.setattr(LiveBundle, "model_dump", lambda self, **kw: pytest.fail("live re-serialised per request"))
    first = client.get("/api/live").json()
    second = client.get("/api/live").json()
    assert first["bundle"] == second["bundle"] and len(calls) <= 1
    assert set(first) == {"bundle", "age_s"} and isinstance(first["age_s"], float)


def test_seven_day_history_is_fifteen_minute_buckets(client):
    week = client.get(f"/api/pair/{_p(CPI_KEY)}/history", params={"range": "7d"}).json()
    # the sample holds two hours of one-minute rows: 7d returns 15-minute averages, 24h keeps every row
    assert week["ts"] == [f"2026-10-20T{h}:{m}:00Z" for h in ("14", "15") for m in ("00", "15", "30", "45")]
    day = client.get(f"/api/pair/{_p(CPI_KEY)}/history", params={"range": "24h"}).json()
    first = [x for t, x in zip(day["ts"], day["net_edge_cents"]) if t < "2026-10-20T14:15" and x is not None]
    assert week["net_edge_cents"][0] == pytest.approx(sum(first) / len(first))


def _daily(tmp_path: Path) -> Path:
    import pyarrow as pa
    import pyarrow.parquet as pq

    shutil.copytree(SAMPLE, tmp_path, dirs_exist_ok=True)
    rows = [{"ts": datetime(2026, 9, 1, h, tzinfo=UTC), "key": CPI_KEY, "kind": "pair", "direction": "k_yes_pm_no",
             "net_edge_cents": float(h), "capacity_usd": 1.0, "k_ask": None, "k_size": None, "pm_ask": None,
             "pm_size": None, "fees_cents": 1.0, "basis_bps": None, "net_bps": None} for h in (3, 4)]
    pq.write_table(pa.Table.from_pylist(rows), tmp_path / "history" / "daily-20260901.parquet")
    return tmp_path


def test_all_history_reads_the_daily_rollups_when_present(monkeypatch, tmp_path):
    c = _client(monkeypatch, _daily(tmp_path))
    every = c.get(f"/api/pair/{_p(CPI_KEY)}/history", params={"range": "all"}).json()
    # rollups reach back past the hour files (the VM keeps ~9 days of those, the rollups 180)
    assert every["ts"] == ["2026-09-01T03:00:00Z", "2026-09-01T04:00:00Z"] and every["net_edge_cents"] == [3.0, 4.0]
    # the ranged views still read the one-minute hour files
    assert len(c.get(f"/api/pair/{_p(CPI_KEY)}/history", params={"range": "1h"}).json()["ts"]) == 61


def test_history_responses_are_cached_until_the_hour_files_change(monkeypatch, tmp_path):
    shutil.copytree(SAMPLE, tmp_path, dirs_exist_ok=True)
    c = _client(monkeypatch, tmp_path)
    calls = []
    real = app_mod.store.history
    monkeypatch.setattr(app_mod.store, "history", lambda k, r: calls.append((k, r)) or real(k, r))
    url = f"/api/pair/{_p(CPI_KEY)}/history"
    a = c.get(url, params={"range": "24h"})
    b = c.get(url, params={"range": "24h"})
    assert a.json() == b.json() and len(calls) == 1
    newest = sorted((tmp_path / "history").glob("pairs-*.parquet"))[-1]
    os.utime(newest, ns=(newest.stat().st_atime_ns, newest.stat().st_mtime_ns + 10**9))  # the publisher rewrote it
    c.get(url, params={"range": "24h"})
    assert len(calls) == 2


def test_cache_headers(client):
    assert "max-age" in client.get("/api/live").headers["cache-control"]
    assert "max-age" in client.get(f"/api/pair/{_p(CPI_KEY)}/history", params={"range": "24h"}).headers["cache-control"]
    assert "cache-control" not in client.get("/healthz").headers  # a health probe always sees the server itself
