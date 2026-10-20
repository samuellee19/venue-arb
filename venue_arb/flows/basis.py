"""Basis flow: Kalshi BRTI settles vs Binance candles -> venue_arb.basis (ClickHouse) and the bound B, via qa.

    secrets uv run python -m venue_arb.flows.basis

Incremental and idempotent: re-reads from 3 h before the newest stored instant (or `days` back on a first run) and
rewrites the same (asset, instant) keys. Binance days the archive has not published yet are skipped until a later run.
"""

import logging
import uuid
from datetime import UTC, datetime, timedelta

from prefect import flow, get_run_logger
from prefect.runtime import flow_run
from qa import checks, db, sql, store

from venue_arb import basis
from venue_arb.config import PIPELINE_PREFIX, SQL_CHECKS
from venue_arb.sources import binance_archive, kalshi

PIPELINE = f"{PIPELINE_PREFIX}_basis"
TABLE = "venue_arb.basis"
SERIES = {"KXBTCD": ("BTC", "BTCUSDT"), "KXETHD": ("ETH", "ETHUSDT")}
CONTRACT = {"instant": "datetime", "asset": "str", "brti": "float", "binance": "float", "diff": "float",
            "binance_1m": "float", "diff_1m": "float"}
COLUMNS = list(CONTRACT)
logging.getLogger("httpx").setLevel(logging.WARNING)


def candles(symbol: str, interval: str, asset: str, seconds: int, instants: list[datetime]) -> dict:
    """Candle closes (keyed by end) for every UTC day that holds a candle ending at one of `instants`."""
    days = {(t - timedelta(seconds=seconds)).date() for t in instants}
    out = {}
    for d in sorted(days):
        got = binance_archive.klines(symbol, interval, d)
        if got:  # None: not published yet
            out |= basis.by_end(asset, got, seconds)
    return out


@flow(name=PIPELINE)
def basis_flow(days: int = 30) -> dict:
    log = get_run_logger()
    run_id = str(flow_run.get_id() or uuid.uuid4())
    results: list[checks.Result] = []
    try:
        ch = db.ch()
        since = datetime.now(UTC) - timedelta(days=days)
        (newest,) = ch.query(f"SELECT max(instant) FROM {TABLE}").result_rows[0]
        if newest.year > 1970:
            since = max(since, newest.replace(tzinfo=UTC) - timedelta(hours=3))
        brti = {}
        for series in SERIES:
            brti |= basis.brti_by_instant(kalshi.fetch_settled(series, int(since.timestamp())))
        rows = []
        for series, (asset, symbol) in SERIES.items():
            ts = sorted(t for a, t in brti if a == asset)
            rows += basis.join({k: v for k, v in brti.items() if k[0] == asset},
                               candles(symbol, "1h", asset, 3600, ts), candles(symbol, "1m", asset, 60, ts))
        pre = [checks.expect_schema(rows, CONTRACT, TABLE)]
        if brti:
            pre.append(checks.expect_rows(len(rows), TABLE))
        results += pre
        checks.enforce(pre)
        if rows:
            ch.insert(TABLE, [[r[c] for c in COLUMNS] for r in rows], column_names=COLUMNS)
        results += sql.run_all(pattern="basis_*.sql", directory=SQL_CHECKS)
        checks.enforce(results)
        out = {"brti_instants": len(brti), "rows": len(rows), "bound": bounds(ch, days)}
        log.info("basis ok: %s", out)
        return out
    except checks.DataQualityError as e:
        results += [r for r in e.failures if r not in results]
        log.error("QA failed: %s", e)
        raise
    except Exception as e:
        results.append(checks.Result("flow_exception", False, detail=type(e).__name__, target=PIPELINE))
        raise
    finally:
        with db.pg() as conn:
            store.record(conn, PIPELINE, run_id, results)


def bounds(ch, days: int = 30) -> dict[str, float]:
    """B per asset from the stored last `days` days (hourly-rule 1h diffs)."""
    rs = ch.query(f"SELECT asset, groupArray(diff) FROM {TABLE} FINAL "
                  "WHERE instant >= now() - INTERVAL {d:UInt32} DAY GROUP BY asset", parameters={"d": days}).result_rows
    return {a: basis.bound(d) for a, d in rs}


if __name__ == "__main__":
    basis_flow()
