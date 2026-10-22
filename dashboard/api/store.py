"""Bundle I/O for the dashboard server: the cached live.json and DuckDB over the history Parquet (no pipeline deps).

Response shapes (dashboard/static reads them):
- history(key, range_) -> columns -> lists, every list the same length, oldest first:
  {"ts": ["2026-10-20T14:00:00Z", ...], "net_edge_cents": [...], "capacity_usd": [...], "k_ask": [...],
   "k_size": [...], "pm_ask": [...], "pm_size": [...], "fees_cents": [...]}; values may be null.
  "1h"/"24h"/"7d" = rows with ts >= (newest ts in the whole history) - interval, inclusive; "all" = hourly averages,
  ts = the hour. {} when there is no history (no history/ dir, no files, or no rows for the key).
- snapshot(key, ts) -> the same columns as one row {"ts": ..., "net_edge_cents": ..., ...}, the row nearest ts
  within 90 s, or None.
Timestamps are UTC ISO strings with a Z suffix.
"""
import logging
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

import duckdb

from venue_arb.bundle import LiveBundle

log = logging.getLogger(__name__)
COLS = ("net_edge_cents", "capacity_usd", "k_ask", "k_size", "pm_ask", "pm_size", "fees_cents", "basis_bps", "net_bps")
RANGES = {"1h": "1 hour", "24h": "24 hours", "7d": "7 days"}
SNAPSHOT_MAX_S = 90
ISO = "strftime(ts, '%Y-%m-%dT%H:%M:%SZ')"


class NoBundle(Exception):
    """live.json has never parsed."""


class Store:
    def __init__(self, bundle_dir: Path):
        self.dir = Path(bundle_dir)
        self._bundle: LiveBundle | None = None
        self._sig: tuple[int, int] | None = None  # (mtime_ns, size) of the live.json behind _bundle
        self._con = duckdb.connect()

    def live(self) -> tuple[LiveBundle, float]:
        """(bundle, age_s); re-reads when live.json's mtime or size changes; keeps the last good bundle on a bad file."""
        path = self.dir / "live.json"
        try:
            st = path.stat()
            sig = (st.st_mtime_ns, st.st_size)
            if sig != self._sig:
                self._sig = sig  # a bad file is not retried until it changes
                self._bundle = LiveBundle.model_validate_json(path.read_bytes())
        except Exception:  # missing, half-written or invalid: serve the last good one
            log.exception("live.json unreadable; keeping the last good bundle")
        if self._bundle is None:
            raise NoBundle("no bundle yet")
        return self._bundle, time.time() - self._bundle.computed_at.timestamp()

    def _src(self) -> str | None:
        hist = self.dir / "history"
        if not any(hist.glob("pairs-*.parquet")):
            return None
        glob = str(hist / "pairs-*.parquet").replace("'", "''")
        # ts as naive UTC: cursors do not inherit a session TimeZone, and TIMESTAMPTZ formats in local time;
        # union_by_name: an hour file written before a column was added still reads (the missing column is null).
        # When no file has a column yet (all written before it was added), union_by_name cannot supply it: add it null.
        read = f"read_parquet('{glob}', union_by_name=true)"
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
        if (src := self._src()) is None:
            return {}
        if range_ == "all":
            avgs = ", ".join(f"avg({c}) AS {c}" for c in COLS)
            sql = (f"SELECT {ISO} AS ts, {', '.join(COLS)} FROM (SELECT date_trunc('hour', ts) AS ts, {avgs} "
                   f"FROM {src} WHERE key = ? GROUP BY 1) ORDER BY 1")
        else:
            sql = (f"SELECT {ISO} AS ts, {', '.join(COLS)} FROM {src} WHERE key = ? "
                   f"AND ts >= (SELECT max(ts) FROM {src}) - INTERVAL '{RANGES[range_]}' ORDER BY ts")
        names, rows = self._query(sql, [key])
        return {n: [r[i] for r in rows] for i, n in enumerate(names)} if rows else {}

    def snapshot(self, key: str, ts: datetime) -> dict | None:
        if (src := self._src()) is None:
            return None
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=UTC)  # a naive ts is UTC, never the server's local time
        sql = (f"SELECT {ISO} AS ts, {', '.join(COLS)} FROM {src} WHERE key = ? "
               f"AND abs(epoch(ts) - ?) <= {SNAPSHOT_MAX_S} ORDER BY abs(epoch(ts) - ?), ts LIMIT 1")
        names, rows = self._query(sql, [key, ts.timestamp(), ts.timestamp()])
        return dict(zip(names, rows[0])) if rows else None
