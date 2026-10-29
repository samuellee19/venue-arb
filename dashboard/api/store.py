"""Bundle I/O for the dashboard server: the cached live.json and DuckDB over the history Parquet (no pipeline deps).

Response shapes (dashboard/static reads them):
- history(key, range_) -> columns -> lists, every list the same length, oldest first:
  {"ts": ["2026-10-20T14:00:00Z", ...], "net_edge_cents": [...], "capacity_usd": [...], "k_ask": [...],
   "k_size": [...], "pm_ask": [...], "pm_size": [...], "fees_cents": [...]}; values may be null.
  "1h"/"24h"/"7d" = rows with ts >= (newest ts in the whole history) - interval, inclusive; "7d" as 15-minute
  averages (ts = the bucket start), "1h"/"24h" every row; "all" = hourly averages, ts = the hour, read from the daily
  rollups (daily-YYYYMMDD.parquet, 180 days) when any exist, else from the hour files. {} when there is no history (no history/ dir, no files, or no rows for the key).
- snapshot(key, ts) -> the same columns as one row {"ts": ..., "net_edge_cents": ..., ...}, the row nearest ts
  within 90 s, or None.
Timestamps are UTC ISO strings with a Z suffix.
"""
import logging
import os
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Literal

import duckdb

from venue_arb.bundle import LiveBundle

log = logging.getLogger(__name__)
COLS = ("net_edge_cents", "capacity_usd", "k_ask", "k_size", "pm_ask", "pm_size", "fees_cents", "basis_bps", "net_bps")
RANGES = {"1h": "1 hour", "24h": "24 hours", "7d": "7 days"}
SNAPSHOT_MAX_S = 90
SPANS = {"1h": timedelta(hours=1), "24h": timedelta(hours=24), "7d": timedelta(days=7)}
BUCKETS = {"7d": "15 minutes"}  # a chart ~800 px wide cannot show 10,000 one-minute points: 7d sends 672 at most
# the VM is an e2-micro (1 GB, 2 vCPU): bound DuckDB so a burst of history requests cannot take the box down
DUCKDB_CONFIG = {"memory_limit": os.environ.get("DUCKDB_MEMORY_LIMIT", "192MB"), "threads": 1}
ISO = "strftime(ts, '%Y-%m-%dT%H:%M:%SZ')"


class NoBundle(Exception):
    """live.json has never parsed."""


class Store:
    def __init__(self, bundle_dir: Path):
        self.dir = Path(bundle_dir)
        self._bundle: LiveBundle | None = None
        self._sig: tuple[int, int] | None = None  # (mtime_ns, size) of the live.json behind _bundle
        self._json: bytes | None = None  # _bundle serialised once, served to every request until the next bundle
        self._con = duckdb.connect(config=DUCKDB_CONFIG)

    def live(self) -> tuple[LiveBundle, float]:
        """(bundle, age_s); re-reads when live.json's mtime or size changes; keeps the last good bundle on a bad file."""
        path = self.dir / "live.json"
        try:
            st = path.stat()
            sig = (st.st_mtime_ns, st.st_size)
            if sig != self._sig:
                self._sig = sig  # a bad file is not retried until it changes
                bundle = LiveBundle.model_validate_json(path.read_bytes())
                self._bundle, self._json = bundle, bundle.model_dump_json().encode()
        except Exception:  # missing, half-written or invalid: serve the last good one
            log.exception("live.json unreadable; keeping the last good bundle")
        if self._bundle is None:
            raise NoBundle("no bundle yet")
        return self._bundle, time.time() - self._bundle.computed_at.timestamp()

    def live_json(self) -> tuple[bytes, float]:
        """live() with the bundle as JSON bytes, serialised once per bundle rather than once per request."""
        _, age = self.live()
        return self._json, age

    def _files(self, since: datetime | None = None, until: datetime | None = None) -> list[Path]:
        """Hour files (pairs-YYYYMMDDHH.parquet, one per clock hour) whose hour overlaps [since, until], oldest first."""
        lo = f"pairs-{since:%Y%m%d%H}.parquet" if since else ""
        hi = f"pairs-{until:%Y%m%d%H}.parquet" if until else "~"
        return sorted(f for f in (self.dir / "history").glob("pairs-*.parquet") if lo <= f.name <= hi)

    def signature(self) -> tuple:
        """Changes whenever the publisher adds or rewrites a history file (it rewrites the newest two each hour)."""
        files = sorted((self.dir / "history").glob("*.parquet"))
        return (len(files), *((f.name, f.stat().st_mtime_ns) for f in files[-2:])) if files else ()

    def _daily(self) -> list[Path]:
        return sorted((self.dir / "history").glob("daily-*.parquet"))

    @staticmethod
    def _hour(f: Path) -> datetime:
        return datetime.strptime(f.name[6:16], "%Y%m%d%H").replace(tzinfo=UTC)

    def _src(self, files: list[Path]) -> str | None:
        if not files:
            return None
        glob = "[" + ", ".join("'" + str(f).replace("'", "''") + "'" for f in files) + "]"  # a DuckDB list of paths
        # ts as naive UTC: cursors do not inherit a session TimeZone, and TIMESTAMPTZ formats in local time;
        # union_by_name: an hour file written before a column was added still reads (the missing column is null).
        # When no file has a column yet (all written before it was added), union_by_name cannot supply it: add it null.
        read = f"read_parquet({glob}, union_by_name=true)"
        cur = self._con.cursor()  # a cursor per call, as _query
        try:
            have = {r[0] for r in cur.execute(f"DESCRIBE SELECT * FROM {read}").fetchall()}
        except duckdb.Error:  # a corrupt file: the query itself fails the same way, and _query serves no rows
            have = set(COLS)
        finally:
            cur.close()
        extra = "".join(f", NULL::DOUBLE AS {c}" for c in COLS if c not in have)
        return f"(SELECT * REPLACE (timezone('UTC', ts) AS ts){extra} FROM {read})"

    def _query(self, sql: str, params: list) -> tuple[list[str], list[tuple]]:
        """No rows on a DuckDB error (a truncated or corrupt hour file): history() -> {}, snapshot() -> None, not 500."""
        cur = self._con.cursor()  # a cursor per call: the connection is shared by the server's worker threads
        try:
            rows = cur.execute(sql, params).fetchall()
            return [d[0] for d in cur.description], rows
        except duckdb.Error:
            log.exception("history query failed; serving no rows")
            return [], []
        finally:
            cur.close()

    def history(self, key: str, range_: Literal["1h", "24h", "7d", "all"]) -> dict:
        """1h/24h/7d read only the hour files the window covers: the newest row is in the newest hour file, so every
        row with ts >= newest - span sits in a file whose hour is >= that file's hour - span."""
        files = self._files()
        if range_ != "all" and files:
            files = self._files(since=self._hour(files[-1]) - SPANS[range_])
        daily = self._daily() if range_ == "all" else []
        if (src := self._src(daily or files)) is None:
            return {}
        avgs = ", ".join(f"avg({c}) AS {c}" for c in COLS)
        if daily:  # already hourly means, one row per key and hour
            sql = f"SELECT {ISO} AS ts, {', '.join(COLS)} FROM {src} WHERE key = ? ORDER BY ts"
        elif range_ == "all":
            sql = (f"SELECT {ISO} AS ts, {', '.join(COLS)} FROM (SELECT date_trunc('hour', ts) AS ts, {avgs} "
                   f"FROM {src} WHERE key = ? GROUP BY 1) ORDER BY 1")
        elif range_ in BUCKETS:
            sql = (f"SELECT {ISO} AS ts, {', '.join(COLS)} FROM (SELECT time_bucket(INTERVAL '{BUCKETS[range_]}', ts) "
                   f"AS ts, {avgs} FROM {src} WHERE key = ? AND ts >= (SELECT max(ts) FROM {src}) - "
                   f"INTERVAL '{RANGES[range_]}' GROUP BY 1) ORDER BY 1")
        else:
            sql = (f"SELECT {ISO} AS ts, {', '.join(COLS)} FROM {src} WHERE key = ? "
                   f"AND ts >= (SELECT max(ts) FROM {src}) - INTERVAL '{RANGES[range_]}' ORDER BY ts")
        names, rows = self._query(sql, [key])
        return {n: [r[i] for r in rows] for i, n in enumerate(names)} if rows else {}

    def snapshot(self, key: str, ts: datetime) -> dict | None:
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=UTC)  # a naive ts is UTC, never the server's local time
        window = timedelta(seconds=SNAPSHOT_MAX_S)
        if (src := self._src(self._files(since=ts - window, until=ts + window))) is None:
            return None
        sql = (f"SELECT {ISO} AS ts, {', '.join(COLS)} FROM {src} WHERE key = ? "
               f"AND abs(epoch(ts) - ?) <= {SNAPSHOT_MAX_S} ORDER BY abs(epoch(ts) - ?), ts LIMIT 1")
        names, rows = self._query(sql, [key, ts.timestamp(), ts.timestamp()])
        return dict(zip(names, rows[0])) if rows else None
