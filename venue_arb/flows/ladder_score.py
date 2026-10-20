"""Ladder score flow: same-instant crypto ladders' order books -> executable spread edges (ClickHouse) and opportunity
episodes (Postgres, source 'ladder'), via qa.

    secrets uv run python -m venue_arb.flows.ladder_score
"""

import logging
import math
import uuid
from datetime import UTC, datetime, timedelta

from prefect import flow, get_run_logger
from prefect.runtime import flow_run
from qa import checks, db, sql, store

from venue_arb import basis, episodes, ladder_edges, scoring
from venue_arb.config import LADDER_MIN_BASIS, PIPELINE_PREFIX, SQL_CHECKS
from venue_arb.fees import FEE_VERSION
from venue_arb.flows.score import OPEN_START, upsert_opportunities
from venue_arb.matching import crypto_rule
from venue_arb.sources import kalshi

PIPELINE = f"{PIPELINE_PREFIX}_ladder_score"
EDGES = "venue_arb.ladder_edges"
OPPS = "venue_arb.opportunities"
CONTRACT = {"ts": "datetime", "asset": "str", "instant": "datetime", "k_id": "str", "pm_id": "str", "direction": "str",
            "kind": "str", "a": "float", "b": "float", "basis_up": "float", "basis_down": "float", "best_edge": "float",
            "capacity_contracts": "float", "capacity_usd": "float", "avg_edge": "float", "fee_version": "str"}
COLUMNS = list(CONTRACT)
# every ladder whose instant is still ahead at the read-back start; old listings rows stay in the table, so no last_seen
LISTINGS_SQL = """
SELECT venue, market_id, event_id, category, title, yes_label, close_time, yes_token,
       raw->'event'->>'series_ticker' AS series, raw->'market'->'feeSchedule' AS fee_schedule,
       (raw->'market'->>'feesEnabled')::boolean AS fees_enabled
FROM venue_arb.listings WHERE category = 'crypto_price' AND close_time > %(start)s
"""
# 30-day directional bounds as of `end`, so a re-run of the same window sees the same bounds
BASIS_SQL = ("SELECT asset, diff FROM venue_arb.basis WHERE instant > {end:DateTime('UTC')} - INTERVAL 30 DAY "
             "AND instant <= {end:DateTime('UTC')}")
READBACK_CAP = timedelta(hours=26)
# hundreds of strike ids: client-side binding (%(x)s) puts them in the POST body, not the URL
BOOKS_SQL = """
SELECT ts, toString(venue), market_id, toString(side), level, price, size FROM venue_arb.book_levels_v2 FINAL
WHERE ts >= fromUnixTimestamp64Milli(%(start)s, 'UTC') AND ts < fromUnixTimestamp64Milli(%(end)s, 'UTC')
  AND ((venue = 'kalshi' AND market_id IN %(k)s) OR (venue = 'polymarket' AND market_id IN %(p)s))
"""
STORED_SQL = f"""
SELECT count() FROM {EDGES} FINAL
WHERE ts >= fromUnixTimestamp64Milli(%(lo)s, 'UTC') AND ts <= fromUnixTimestamp64Milli(%(hi)s, 'UTC')
  AND (k_id, pm_id, direction) IN %(keys)s
"""
logging.getLogger("httpx").setLevel(logging.WARNING)


def _ms(t: datetime) -> int:
    return round(t.timestamp() * 1000)


def bounds_by_asset(rows) -> dict[str, tuple[float, float]]:
    """(up, down) per asset with >= LADDER_MIN_BASIS finite diffs; an asset without them has no proof and is skipped."""
    diffs: dict[str, list[float]] = {}
    for asset, diff in rows:
        if diff is not None and math.isfinite(diff):
            diffs.setdefault(asset, []).append(diff)
    return {a: basis.bounds(d) for a, d in diffs.items() if len(d) >= LADDER_MIN_BASIS}


@flow(name=PIPELINE)
def ladder_score_flow(window_hours: int = 3, end: datetime | None = None) -> dict:
    """Score the `window_hours` before `end` (default now); overlapping windows rewrite the same keys.
    ponytail: 3 h, not the score flow's 26 h: re-scoring a day of every ladder's books hourly buys nothing. An outage
    longer than 3 h leaves books unscored; re-run once with a wider window. Ladders are read and written one at a time,
    so memory is bounded by the largest ladder's books and rows."""
    log = get_run_logger()
    run_id = str(flow_run.get_id() or uuid.uuid4())
    results: list[checks.Result] = []
    try:
        end = end or datetime.now(UTC)
        window_start = start = end - timedelta(hours=window_hours)
        with db.pg() as conn:
            (open_start,) = conn.execute(
                OPEN_START, {"start": start, "gap": episodes.MAX_GAP_S, "source": "ladder"}).fetchone()
            # ponytail: read back to an open episode's start so it keeps its key, capped at READBACK_CAP (26 h) before
            # `end`; an episode open longer than that is re-keyed at the cap (same ceiling as the score flow's window)
            if open_start and open_start < start:
                start = max(open_start, end - READBACK_CAP)
            cur = conn.execute(LISTINGS_SQL, {"start": start})
            cols = [c.name for c in cur.description]
            listing = [dict(zip(cols, r, strict=True)) for r in cur.fetchall()]
        meta = {(r["venue"], r["market_id"]): r for r in listing}
        found = crypto_rule.ladders(listing)
        ch = db.ch()
        bounds = bounds_by_asset(ch.query(BASIS_SQL, parameters={"end": end}).result_rows)
        fees: dict[str, dict] = {}

        def k_fee(market_id: str) -> dict:  # Kalshi series fee, fetched once per series per run
            series = meta[("kalshi", market_id)]["series"]
            if series not in fees:
                fees[series] = kalshi.fetch_series(series)
            return fees[series]

        def pm_fee(market_id: str) -> tuple[dict | None, bool]:  # the market's own feeSchedule
            m = meta[("polymarket", market_id)]
            return m["fee_schedule"], bool(m["fees_enabled"])

        stats = {"ladders": len(found), "ladders_no_basis": 0, "ladders_settled_in_readback": 0,
                 "kalshi_strikes_booked": 0, "pm_strikes_booked": 0, "pairs_scored": 0, "admissible_checks": 0,
                 "edge_rows": 0, "arb_rows": 0, "rows_with_capacity": 0, "episodes": 0}
        stored_total = 0
        opps = []
        for (asset, instant), lad in sorted(found.items()):
            if instant <= window_start:  # settled before the window: only listed because the read-back reached it
                stats["ladders_settled_in_readback"] += 1
                continue
            if asset not in bounds:
                stats["ladders_no_basis"] += 1
                continue
            snaps = scoring.snapshots(ch.query(BOOKS_SQL, parameters={
                "start": _ms(start), "end": _ms(min(end, instant)),
                "k": [s.market_id for s in lad["kalshi"]], "p": [s.market_id for s in lad["polymarket"]]}).result_rows)
            rows, eps, st = ladder_edges.score_ladder(asset, instant, lad, snaps, *bounds[asset], k_fee, pm_fee,
                                                      FEE_VERSION)
            del snaps
            for key, v in st.items():
                stats[key] += v
            pre = [checks.expect_schema(rows, CONTRACT, EDGES, nullable=frozenset({"best_edge", "avg_edge"}))]
            if st["pairs_scored"]:  # booked pairs on both legs must yield rows; none booked (yet) is not an error
                pre.append(checks.expect_rows(len(rows), EDGES))
            checks.enforce(pre)  # a failure raises before this ladder is written; recorded via DataQualityError
            if rows:
                ch.insert(EDGES, [[r[c] for c in COLUMNS] for r in rows], column_names=COLUMNS)
                (stored,) = ch.query(STORED_SQL, parameters={
                    "lo": _ms(min(r["ts"] for r in rows)), "hi": _ms(max(r["ts"] for r in rows)),
                    "keys": sorted({(r["k_id"], r["pm_id"], r["direction"]) for r in rows})}).result_rows[0]
                stored_total += stored
            stats["edge_rows"] += len(rows)
            stats["arb_rows"] += sum(r["kind"] == "arb" for r in rows)
            stats["rows_with_capacity"] += sum(r["capacity_contracts"] > 0 for r in rows)
            stats["episodes"] += len(eps)
            opps += [scoring.opportunity_row(e, source="ladder") for e in eps]
        results.append(checks.Result("expect_schema", True, 0, 0, f"{stats['edge_rows']} rows checked per ladder "
                                     "before insert", target=EDGES))
        # ponytail: 1% slack, as in the score flow, for rows an earlier overlapping window aligned differently
        results.append(checks.expect_count_match(stats["edge_rows"], stored_total, EDGES, tolerance=0.01))
        # denominator for the note: every admissible (a, b, direction) at every aligned snapshot, scored or pruned
        results.append(checks.Result("ladder_admissible_checks", True, stats["admissible_checks"],
                                     detail="admissible strike-pair checks (not stored)", severity="warn",
                                     target=EDGES))
        with db.pg() as conn, conn.transaction():  # one transaction keeps now() fixed for the count
            n = upsert_opportunities(conn, opps)
        results.append(checks.expect_count_match(len(opps), n, OPPS))
        results += sql.run_all(pattern="ladder_edges_*.sql", directory=SQL_CHECKS)
        results += sql.run_all(pattern="opportunities_*.sql", directory=SQL_CHECKS)
        checks.enforce(results)
        log.info("ladder score ok: %s", stats)
        return stats
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
    ladder_score_flow()
