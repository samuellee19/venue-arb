"""Basis flow: Kalshi BRTI settles vs Binance candles -> venue_arb.basis (ClickHouse) and the bounds (up, down), via qa.

    secrets uv run python -m venue_arb.flows.basis

Incremental and idempotent: re-reads the trailing REREAD_DAYS days every run (or `days` on an empty table) and
rewrites the same (asset, instant) keys.

Candle (verified 2026-10-20 in venue_arb.listings.rules): Polymarket hourly = the BTC/USDT 1 hour candle ending at the
hour; Polymarket daily = "the Binance 1 minute candle for BTC/USDT closing at 12:00 in the ET timezone (noon) ... final
'Close' price". The 1m candle closing at H and the 1h candle ending at H share one close (the last trade before H), as the
first live run showed in every row, so only the 1h zips are downloaded and binance_1m/diff_1m are left NaN. Binance days the archive has not published yet are skipped until a later run.
"""

import logging
import uuid
from datetime import UTC, datetime, timedelta

from prefect import flow, get_run_logger
from prefect.runtime import flow_run
from qa import checks, db, sql, store

from venue_arb import basis
from venue_arb.config import BASIS_WINDOW_DAYS, PIPELINE_PREFIX, SQL_CHECKS
from venue_arb.sources import binance_archive, kalshi

PIPELINE = f"{PIPELINE_PREFIX}_basis"
TABLE = "venue_arb.basis"
REREAD_DAYS = 3
FRESH_SLACK = timedelta(days=3)  # Binance archive lag plus a weekend-ish margin
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
        (n,) = ch.query(f"SELECT count() FROM {TABLE}").result_rows[0]
        since = datetime.now(UTC) - timedelta(days=REREAD_DAYS if n else days)
        brti = {}
        for series in SERIES:
            brti |= basis.brti_by_instant(kalshi.fetch_settled(series, int(since.timestamp())))
        rows = []
        for series, (asset, symbol) in SERIES.items():
            ts = sorted(t for a, t in brti if a == asset)
            rows += basis.join({k: v for k, v in brti.items() if k[0] == asset},
                               candles(symbol, "1h", asset, 3600, ts))
        # zero BRTI settles in the trailing window is an error, not a skip: a field rename must be loud
        pre = [checks.expect_rows(len(brti), "kalshi BRTI settles"), checks.expect_schema(rows, CONTRACT, TABLE),
               checks.expect_rows(len(rows), TABLE)]
        results += pre
        checks.enforce(pre)
        if rows:
            ch.insert(TABLE, [[r[c] for c in COLUMNS] for r in rows], column_names=COLUMNS)
            keys = sorted({(r["asset"], int(r["instant"].timestamp())) for r in rows})
            (stored,) = ch.query(f"SELECT count() FROM {TABLE} FINAL WHERE (asset, toUnixTimestamp(instant)) IN %(keys)s",
                                 parameters={"keys": keys}).result_rows[0]
            results.append(checks.expect_count_match(len(keys), stored, TABLE))
            results += [checks.expect_fresh(latest, f"{TABLE}:{asset}", FRESH_SLACK) for asset, latest in ch.query(
                f"SELECT asset, max(instant) FROM {TABLE} FINAL GROUP BY asset ORDER BY asset").result_rows]
        results += sql.run_all(pattern="basis_*.sql", directory=SQL_CHECKS)
        checks.enforce(results)
        out = {"brti_instants": len(brti), "rows": len(rows), "bounds": bounds(ch)}
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


def bounds(ch) -> dict[str, tuple[float, float]]:
    """(up, down) per asset from the stored last BASIS_WINDOW_DAYS days (the window ladder_score and collect read)."""
    rs = ch.query(f"SELECT asset, groupArray(diff) FROM {TABLE} FINAL "
                  f"WHERE instant > now() - INTERVAL {BASIS_WINDOW_DAYS} DAY GROUP BY asset").result_rows
    return {a: basis.bounds(d) for a, d in rs}


if __name__ == "__main__":
    basis_flow()
