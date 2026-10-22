"""Stock-perp score flow: the collector's contexts, books and Schwab quotes -> executable basis edges (ClickHouse) and
opportunity episodes (Postgres, source 'stock_perp'), via qa.

    secrets uv run python -m venue_arb.flows.stock_perp_score
"""

import logging
from datetime import UTC, datetime, timedelta

import obs
from prefect import flow, get_run_logger
from qa import checks, db, sql, store

from venue_arb import episodes
from venue_arb.config import PIPELINE_PREFIX, SQL_CHECKS
from venue_arb.flows.score import OPEN_START, upsert_opportunities
from venue_arb.sources import hyperliquid
from venue_arb.stock_perp_window import score_window, stock_perp_opportunity_row

PIPELINE = f"{PIPELINE_PREFIX}_stock_perp_score"
EDGES = "venue_arb.stock_perp_edges"
OPPS = "venue_arb.opportunities"
CONTRACT = {"ts": "datetime", "symbol": "str", "direction": "str", "ref_kind": "str", "reference": "float",
            "mark": "float", "basis_bps": "float", "perp_fee_bps": "float", "perp_spread_bps": "float",
            "stock_spread_bps": "float", "funding_bps": "float", "borrow_bps": "float", "net_bps": "float",
            "capacity_contracts": "float", "capacity_usd": "float", "fee_version": "str"}
COLUMNS = list(CONTRACT)
WINDOW_HOURS = 3
READBACK_CAP = timedelta(hours=26)
T = "{start:DateTime64(3, 'UTC')} AND ts < {end:DateTime64(3, 'UTC')}"
CTX_SQL = ("SELECT ts, symbol, mark, oracle, mid, funding_h, premium, open_interest, day_volume_usd, growth_mode "
           f"FROM venue_arb.stock_perp_ctx WHERE ts >= {T} ORDER BY symbol, ts")
BOOKS_SQL = ("SELECT ts, symbol, side, level, price, size FROM venue_arb.stock_perp_books "
             f"WHERE symbol = {{s:String}} AND ts >= {T}")
REFS_SQL = ("SELECT ts, symbol, ref_kind, ref_symbol, bid, ask, bid_size, ask_size, last, quote_time "
            f"FROM venue_arb.stock_perp_refs WHERE symbol = {{s:String}} AND ref_kind = 'schwab' AND ts >= {T}")
STORED_SQL = (f"SELECT count() FROM {EDGES} FINAL WHERE symbol = {{s:String}} AND ts >= {{lo:DateTime64(3, 'UTC')}} "
              "AND ts <= {hi:DateTime64(3, 'UTC')}")
logging.getLogger("httpx").setLevel(logging.WARNING)


def _dicts(res) -> list[dict]:
    return [dict(zip(res.column_names, r, strict=True)) for r in res.result_rows]


@flow(name=PIPELINE)
@obs.job("venue-arb.stock_perp_score")
def stock_perp_score_flow(window_hours: int = WINDOW_HOURS, end: datetime | None = None, pipeline: str = PIPELINE) -> dict:
    """Score the `window_hours` before `end` (default now); overlapping windows rewrite the same edge keys and upsert
    the same episodes. `pipeline` names the qa results (tests use their own).
    ponytail: 3 h like the ladder flow; an outage longer than that leaves ticks unscored until a re-run with a wider
    window. Books and refs are read one symbol at a time, so memory is bounded by one symbol's rows."""
    log = get_run_logger()
    run_id = obs.current().id
    results: list[checks.Result] = []
    try:
        end = end or datetime.now(UTC)
        start = end - timedelta(hours=window_hours)
        with db.pg() as conn:
            (open_start,) = conn.execute(
                OPEN_START, {"start": start, "gap": episodes.MAX_GAP_S, "source": "stock_perp"}).fetchone()
        # ponytail: read back to an open episode's start so it keeps its key, capped at READBACK_CAP before `end`
        if open_start and open_start < start:
            start = max(open_start, end - READBACK_CAP)
        labels = hyperliquid.load_labels()
        ch = db.ch()
        ctx_rows = _dicts(ch.query(CTX_SQL, parameters={"start": start, "end": end}))
        by_symbol: dict[str, list[dict]] = {}
        for c in ctx_rows:
            by_symbol.setdefault(c["symbol"], []).append(c)
        del ctx_rows
        stats = {"symbols": 0, "snapshots": 0, "edge_rows": 0, "rows_with_capacity": 0, "episodes": 0,
                 "oracle_only_rows": 0}
        stored_total = 0
        opps = []
        for symbol, ctxs in sorted(by_symbol.items()):
            p = {"s": symbol, "start": start, "end": end}
            rows, eps, st = score_window(ctxs, _dicts(ch.query(BOOKS_SQL, parameters=p)),
                                         _dicts(ch.query(REFS_SQL, parameters=p)), labels, start=start, end=end)
            for key, v in st.items():
                stats[key] += v
            pre = [checks.expect_schema(rows, CONTRACT, EDGES, nullable=frozenset({"stock_spread_bps"}))]
            if st["snapshots"]:  # contexts without any book rows are the collector's books-fresh check to catch
                pre.append(checks.expect_rows(len(rows), EDGES))
            checks.enforce(pre)  # a failure raises before this symbol is written
            if rows:
                ch.insert(EDGES, [[r[c] for c in COLUMNS] for r in rows], column_names=COLUMNS)
                (stored,) = ch.query(STORED_SQL, parameters={"s": symbol, "lo": min(r["ts"] for r in rows),
                                                             "hi": max(r["ts"] for r in rows)}).result_rows[0]
                stored_total += stored
            del rows
            opps += [stock_perp_opportunity_row(e, hyperliquid.label_or_none(e.kalshi_id, labels)) for e in eps]
        results.append(checks.Result("expect_schema", True, 0, 0, f"{stats['edge_rows']} rows checked per symbol "
                                     "before insert", target=EDGES))
        # ponytail: 1% slack, as in the ladder flow, for rows an earlier overlapping window aligned differently
        results.append(checks.expect_count_match(stats["edge_rows"], stored_total, EDGES, tolerance=0.01))
        with db.pg() as conn, conn.transaction():  # one transaction keeps now() fixed for the count
            n = upsert_opportunities(conn, opps)
        results.append(checks.expect_count_match(len(opps), n, OPPS))
        # not stock_perp_*.sql: the books-fresh check belongs to the collector
        results += sql.run_all(pattern="stock_perp_edges_*.sql", directory=SQL_CHECKS)
        results += sql.run_all(pattern="opportunities_*.sql", directory=SQL_CHECKS)
        checks.enforce(results)
        log.info("stock perp score ok: %s", stats)
        return stats
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
    stock_perp_score_flow()
