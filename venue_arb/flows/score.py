"""Score flow: exact pairs' order books -> executable edges (ClickHouse) and opportunity episodes (Postgres), via qa.

    secrets uv run python -m venue_arb.flows.score
"""

import logging
from datetime import UTC, datetime, timedelta

import obs
from prefect import flow, get_run_logger
from qa import checks, db, sql, store

from venue_arb import episodes, scoring
from venue_arb.config import PIPELINE_PREFIX, SQL_CHECKS
from venue_arb.exact import eligible
from venue_arb.fees import FEE_VERSION
from venue_arb.flows.exact_sql import load_eligible
from venue_arb.sources import kalshi

PIPELINE = f"{PIPELINE_PREFIX}_score"
EDGES = "venue_arb.edges"
OPPS = "venue_arb.opportunities"
CONTRACT = {"ts": "datetime", "kalshi_id": "str", "pm_id": "str", "direction": "str", "best_edge": "float",
            "capacity_contracts": "float", "capacity_usd": "float", "avg_edge": "float", "fee_version": "str"}
COLUMNS = list(CONTRACT)
OPP_COLS = ("kalshi_id", "pm_id", "direction", "start_ts", "end_ts", "snapshots", "max_edge", "median_capacity_usd",
            "annualized", "us_executable", "transfer_cost_usd", "source")
BOOKS_SQL = """
SELECT ts, toString(venue), market_id, toString(side), level, price, size FROM venue_arb.book_levels_v2 FINAL
WHERE ts >= {start:DateTime64(3, 'UTC')} AND ts < {end:DateTime64(3, 'UTC')}
  AND ((venue = 'kalshi' AND market_id IN {k:Array(String)}) OR (venue = 'polymarket' AND market_id IN {p:Array(String)}))
"""
UPSERT = (f"INSERT INTO {OPPS} ({', '.join(OPP_COLS)}) VALUES ({', '.join(['%s'] * len(OPP_COLS))}) "
          "ON CONFLICT (kalshi_id, pm_id, direction, start_ts) DO UPDATE SET "
          + ", ".join(f"{c} = EXCLUDED.{c}" for c in OPP_COLS[4:]) + ", scored_at = now()")
# an episode still open at the window start: read books back to its start so it re-forms under the same key
OPEN_START = (f"SELECT min(start_ts) FROM {OPPS} "
              "WHERE source = %(source)s AND end_ts >= %(start)s - make_interval(secs => %(gap)s)")
logging.getLogger("httpx").setLevel(logging.WARNING)


# a re-score can align the same live episode to a different first snapshot (window start, late book rows), so the
# row that overlaps the new episode under another start_ts is the same episode: replace it, don't keep both
# (sql_checks/opportunities_no_overlap.sql is the guard)
MERGE_OVERLAP = (f"DELETE FROM {OPPS} WHERE kalshi_id = %(kalshi_id)s AND pm_id = %(pm_id)s AND direction = %(direction)s "
                 "AND source = %(source)s AND start_ts <> %(start_ts)s AND start_ts <= coalesce(%(end_ts)s, now()) "
                 "AND coalesce(end_ts, now()) >= %(start_ts)s")


def upsert_opportunities(conn, opps: list[dict]) -> int:
    """Upsert episodes, replacing any row of the same pair, direction and source that overlaps a new episode under a
    different start_ts; returns rows written in this call. Caller holds one transaction so now() is fixed."""
    cur = conn.cursor()
    cur.executemany(MERGE_OVERLAP, [{k: o[k] for k in ("kalshi_id", "pm_id", "direction", "source", "start_ts", "end_ts")}
                                    for o in opps])
    cur.executemany(UPSERT, [tuple(o[c] for c in OPP_COLS) for o in opps])
    (n,) = conn.execute(f"SELECT count(*) FROM {OPPS} WHERE scored_at = now()").fetchone()
    return n


@flow(name=PIPELINE)
@obs.job("venue-arb.score")
def score_flow(window_hours: int = 26, end: datetime | None = None) -> dict:
    """Score the `window_hours` before `end` (default now). Windows overlap run to run; re-scoring rewrites the same
    edge keys and upserts the same episodes."""
    log = get_run_logger()
    run_id = obs.current().id
    results: list[checks.Result] = []
    try:
        end = end or datetime.now(UTC)
        start = end - timedelta(hours=window_hours)
        with db.pg() as conn:
            pairs = eligible(load_eligible(conn))
            (open_start,) = conn.execute(OPEN_START, {"start": start, "gap": episodes.MAX_GAP_S, "source": "pair"}).fetchone()
        # ponytail: an episode first seen already truncated (first run, or an outage longer than the window) keeps
        # its truncated start; later runs extend that row. Full history needs one re-score from the books' start.
        if open_start and open_start < start:
            start = open_start
        fees = {s: kalshi.fetch_series(s) for s in {p.kalshi_series for p in pairs}}
        ch = db.ch()
        snaps = scoring.snapshots(ch.query(BOOKS_SQL, parameters={
            "start": start, "end": end, "k": [p.kalshi_id for p in pairs], "p": [p.pm_id for p in pairs]}
        ).result_rows) if pairs else {}
        rows, eps, unbooked = [], [], 0
        for p in pairs:
            k, pm = snaps.get(("kalshi", p.kalshi_id)), snaps.get(("polymarket", p.pm_id))
            if not (k and pm):
                unbooked += 1  # a leg without snapshots in the window contributes nothing; counted, not an error
                continue
            for direction, pts in scoring.pair_points(p, k, pm, fees[p.kalshi_series]).items():
                rows += [scoring.edge_row(p, t, e, FEE_VERSION) for t, e in pts]
                eps += episodes.find(pts, p.close_time, kalshi_id=p.kalshi_id, pm_id=p.pm_id, direction=direction)
        pre = [checks.expect_schema(rows, CONTRACT, EDGES, nullable=frozenset({"best_edge", "avg_edge"}))]
        if pairs:
            pre.append(checks.expect_rows(len(rows), EDGES))
        results += pre
        checks.enforce(pre)
        if rows:
            ch.insert(EDGES, [[r[c] for c in COLUMNS] for r in rows], column_names=COLUMNS)
            lo, hi = min(r["ts"] for r in rows), max(r["ts"] for r in rows)
            (stored,) = ch.query(
                f"SELECT count() FROM {EDGES} FINAL WHERE ts >= {{lo:DateTime64(3, 'UTC')}} "
                "AND ts <= {hi:DateTime64(3, 'UTC')} AND (kalshi_id, pm_id) IN {ids:Array(Tuple(String, String))}",
                parameters={"lo": lo, "hi": hi, "ids": sorted({(r["kalshi_id"], r["pm_id"]) for r in rows})},
            ).result_rows[0]
            # ponytail: 1% slack for rows an earlier, overlapping window aligned differently (left in place)
            results.append(checks.expect_count_match(len(rows), stored, EDGES, tolerance=0.01))
        opps = [scoring.opportunity_row(e) for e in eps]
        with db.pg() as conn, conn.transaction():  # qa.db is autocommit; one transaction keeps now() fixed
            n = upsert_opportunities(conn, opps)
        results.append(checks.expect_count_match(len(opps), n, OPPS))
        results += sql.run_all(pattern="edges_*.sql", directory=SQL_CHECKS)
        results += sql.run_all(pattern="opportunities_*.sql", directory=SQL_CHECKS)
        checks.enforce(results)
        out = {"pairs": len(pairs), "edge_rows": len(rows), "episodes": len(eps), "pairs_without_books": unbooked}
        log.info("score ok: %s", out)
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
    score_flow()
