"""Screened-tier flow: hourly history gaps for exact pairs (ClickHouse), via qa. Run by hand, not scheduled.

    secrets uv run python -m venue_arb.flows.screened

History is a traded/mid price, not an ask: these rows are screened only, never executable (see score flow).
"""

import logging
import uuid
from datetime import UTC, datetime

from prefect import flow, get_run_logger
from prefect.runtime import flow_run
from qa import checks, db, store

from venue_arb import screened
from venue_arb.config import PIPELINE_PREFIX
from venue_arb.exact import eligible
from venue_arb.flows.exact_sql import load_eligible
from venue_arb.sources import kalshi, polymarket

PIPELINE = f"{PIPELINE_PREFIX}_screened"
TABLE = "venue_arb.screened"
CONTRACT = {"ts": "datetime", "kalshi_id": "str", "pm_id": "str", "direction": "str", "k_price": "float",
            "pm_price": "float", "gross_gap": "float", "net_gap_top": "float"}
COLUMNS = list(CONTRACT)
# per listing: its earliest sighting, and the venue's own open time where the raw payload has one
START_SQL = """
SELECT market_id, min(first_seen), min(raw->'market'->>'open_time'), min(raw->'market'->>'startDate')
FROM venue_arb.listings WHERE (venue = 'kalshi' AND market_id = ANY(%(k)s)) OR (venue = 'polymarket' AND market_id = ANY(%(p)s))
GROUP BY venue, market_id
"""
logging.getLogger("httpx").setLevel(logging.WARNING)


def _start(first_seen, opened) -> datetime:
    return datetime.fromisoformat(opened) if opened else first_seen


@flow(name=PIPELINE)
def screened_flow(end: datetime | None = None) -> dict:
    log = get_run_logger()
    run_id = str(flow_run.get_id() or uuid.uuid4())
    results: list[checks.Result] = []
    try:
        end = end or datetime.now(UTC)
        with db.pg() as conn:
            pairs = eligible(load_eligible(conn))
            starts = {r[0]: _start(r[1], r[2] or r[3]) for r in conn.execute(
                START_SQL, {"k": [p.kalshi_id for p in pairs], "p": [p.pm_id for p in pairs]}).fetchall()}
        fees = {s: kalshi.fetch_series(s) for s in {p.kalshi_series for p in pairs}}
        rows, per_pair = [], {}
        for p in pairs:
            start = int(max(starts[p.kalshi_id], starts[p.pm_id]).timestamp())
            k = kalshi.fetch_candles(p.kalshi_series, p.kalshi_id, start, int(end.timestamp()))
            pm = polymarket.fetch_price_history(p.pm_yes_token, start, int(end.timestamp()))
            g = screened.gaps(p, k, pm, fees[p.kalshi_series])
            per_pair[p.kalshi_id] = len(g)
            rows += g
        pre = [checks.expect_schema(rows, CONTRACT, TABLE)]
        if pairs:
            pre.append(checks.expect_rows(len(rows), TABLE))
        results += pre
        checks.enforce(pre)
        ch = db.ch()
        if rows:
            ch.insert(TABLE, [[r[c] for c in COLUMNS] for r in rows], column_names=COLUMNS)
            (stored,) = ch.query(
                f"SELECT count() FROM {TABLE} FINAL WHERE (kalshi_id, pm_id) IN {{ids:Array(Tuple(String, String))}}",
                parameters={"ids": sorted({(r["kalshi_id"], r["pm_id"]) for r in rows})}).result_rows[0]
            results.append(checks.expect_count_match(len(rows), stored, TABLE, tolerance=0.01))
        checks.enforce(results)
        out = {"pairs": len(pairs), "rows": len(rows), "rows_per_pair": per_pair}
        log.info("screened ok: %s", out)
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


if __name__ == "__main__":
    screened_flow()
