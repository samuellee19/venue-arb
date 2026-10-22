"""Stock-perp weekend flow: for each weekend of the last 60 days without a finished row, the perp's Friday, Saturday
and Sunday marks and Monday's stock opens -> venue_arb.stock_perp_weekends, via qa. Runs Monday after the open.

    secrets uv run python -m venue_arb.flows.stock_perp_weekend
"""

import logging
from datetime import UTC, datetime, timedelta

import obs
from prefect import flow, get_run_logger
from qa import checks, db, store

from venue_arb import weekends
from venue_arb.config import PIPELINE_PREFIX
from venue_arb.sources import hyperliquid

PIPELINE = f"{PIPELINE_PREFIX}_stock_perp_weekend"
TABLE = "venue_arb.stock_perp_weekends"
COLUMNS = ["symbol", "friday", "p_fri", "p_sat", "p_sun", "p_open_pre", "p_open_reg", "ref_kind"]
CH_FROM, CH_TO = "{lo:DateTime64(3, 'UTC')}", "{hi:DateTime64(3, 'UTC')}"
CTX_SQL = f"SELECT symbol, ts, mark, oracle FROM venue_arb.stock_perp_ctx WHERE ts >= {CH_FROM} AND ts < {CH_TO}"
CANDLES_SQL = ("SELECT symbol, hour, close FROM venue_arb.stock_perp_candles FINAL "
               "WHERE hour >= {lo:DateTime('UTC')} AND hour < {hi:DateTime('UTC')}")
REFS_SQL = ("SELECT symbol, ref_kind, ts, bid, ask FROM venue_arb.stock_perp_refs "
            f"WHERE ref_kind IN ('schwab', 'schwab_daily') AND ts >= {CH_FROM} AND ts < {CH_TO}")
# finished: the open this label's reference kind can give is there; anything else is re-tried on the next run
DONE_SQL = ("SELECT symbol, friday FROM venue_arb.stock_perp_weekends WHERE friday >= %(lo)s AND "
            "((ref_kind = 'schwab' AND p_open_reg IS NOT NULL) OR (ref_kind = 'oracle_krx' AND p_open_pre IS NOT NULL) "
            "OR ref_kind = 'none')")
UPSERT = (f"INSERT INTO {TABLE} ({', '.join(COLUMNS)}) VALUES ({', '.join('%(' + c + ')s' for c in COLUMNS)}) "
          "ON CONFLICT (symbol, friday) DO UPDATE SET p_fri = EXCLUDED.p_fri, p_sat = EXCLUDED.p_sat, "
          "p_sun = EXCLUDED.p_sun, p_open_pre = EXCLUDED.p_open_pre, p_open_reg = EXCLUDED.p_open_reg, "
          "ref_kind = EXCLUDED.ref_kind, computed_at = now()")
ALL_SQL = f"SELECT {', '.join(COLUMNS)} FROM {TABLE} WHERE friday >= %(lo)s"
logging.getLogger("httpx").setLevel(logging.WARNING)


def _utc(ts: datetime) -> datetime:
    return ts if ts.tzinfo else ts.replace(tzinfo=UTC)


def _mid(bid: float, ask: float) -> float | None:
    return (bid + ask) / 2 if 0 < bid <= ask else None  # two-sided and uncrossed, as the executable tier requires


@flow(name=PIPELINE)
@obs.job("venue-arb.stock_perp_weekend")
def stock_perp_weekend_flow(end: datetime | None = None, pipeline: str = PIPELINE) -> dict:
    """Fill every (symbol, Friday) of the last 60 days that has no finished row; a finished row is never rewritten, so
    a weekend scored from candles keeps its hourly values. Marks come from the 60 s contexts when they reach back to
    Friday 8 PM, else from the hourly candles (stamped at their close, so a sample never sees a later price).
    ponytail: a weekend's contexts for every symbol are read at once (about 0.5M rows) and the Fridays go one at a
    time; narrow the SQL to the sample windows if that ever matters."""
    log = get_run_logger()
    run_id = obs.current().id
    results: list[checks.Result] = []
    try:
        end = end or datetime.now(UTC)
        fridays = weekends.fridays(end)
        labels = hyperliquid.load_labels()
        out: list[dict] = []
        ch = db.ch()
        with db.pg() as conn:
            done = set(conn.execute(DONE_SQL, {"lo": fridays[0]}).fetchall()) if fridays else set()
        for friday in fridays:
            todo = [s for s in labels if (s, friday) not in done]
            if not todo:
                continue
            fri = weekends.at(friday, weekends.SAMPLES["fri"][1])
            monday = friday + timedelta(days=3)
            lo, hi = fri - timedelta(hours=1), weekends.at(monday, weekends.CLOSE_REG) + timedelta(hours=1)
            p = {"lo": lo, "hi": hi}
            ctx: dict[str, list] = {}
            oracle: dict[str, list] = {}
            for sym, ts, mark, orc in ch.query(CTX_SQL, parameters=p).result_rows:
                ctx.setdefault(sym, []).append((_utc(ts), mark))
                oracle.setdefault(sym, []).append((_utc(ts), orc))
            candles: dict[str, list] = {}
            for sym, hour, close in ch.query(CANDLES_SQL, parameters=p).result_rows:  # stamped at the hour's close
                candles.setdefault(sym, []).append((_utc(hour) + timedelta(hours=1), close))
            live: dict[str, list] = {}
            daily: dict[str, list] = {}
            for sym, kind, ts, bid, ask in ch.query(REFS_SQL, parameters=p).result_rows:
                if (m := _mid(bid, ask)) is not None:
                    (live if kind == "schwab" else daily).setdefault(sym, []).append((_utc(ts), m))
            for sym in todo:
                label = labels[sym]
                from_ctx = bool(ctx.get(sym)) and min(ctx[sym])[0] <= fri
                marks, tol = ((ctx[sym], timedelta(minutes=5)) if from_ctx else
                              (candles.get(sym, []), timedelta(minutes=61)))
                krx = label.exchange == "KRX"
                # the candle table holds no oracle: a KRX weekend without contexts has no Korean open yet and is retried
                refs = (oracle.get(sym) if from_ctx else None) if krx else live.get(sym)
                row = weekends.weekend_row(sym, friday, marks, refs or [], label, tol, daily.get(sym, []))
                if any(row[k] is not None for k in ("p_fri", "p_sat", "p_sun")):  # a symbol not yet listed has no weekend
                    out.append(row)
        with db.pg() as conn, conn.transaction():  # one transaction keeps now() fixed for the count
            conn.cursor().executemany(UPSERT, out)
            (n,) = conn.execute(f"SELECT count(*) FROM {TABLE} WHERE computed_at = now()").fetchone()
        results.append(checks.expect_count_match(len(out), n, TABLE))
        with db.pg() as conn:
            cols = conn.execute(ALL_SQL, {"lo": (end - timedelta(days=60)).date()}).fetchall()
        s = weekends.summary([dict(zip(COLUMNS, r, strict=True)) for r in cols])
        results.append(checks.Result("weekend_summary", True, len(out), detail=str(s), severity="warn", target=TABLE))
        checks.enforce(results)
        log.info("stock perp weekend ok: %d rows written, %s", len(out), s)
        return s
    except checks.DataQualityError as e:
        results += [r for r in e.failures if r not in results]
        log.error("QA failed: %s", e)
        raise
    except Exception as e:
        results.append(checks.Result("flow_exception", False, detail=type(e).__name__, target=pipeline))
        raise
    finally:
        with db.pg() as conn:
            store.record(conn, pipeline, run_id, results)


if __name__ == "__main__":
    stock_perp_weekend_flow()
