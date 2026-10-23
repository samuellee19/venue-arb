"""Private oracle-vs-Schwab check: for each of the last 5 ET days, how far the trade.xyz oracle sat from the live
Schwab mid on US names while the oracle was externally derived. Writes venue_arb.stock_perp_oracle_check and
data/private/oracle-check-YYYY-MM-DD.md (git-ignored), warns on drift. Schwab data is for personal use: nothing
here is read by the public publisher. Runs after the US post-market close.

    secrets uv run python -m venue_arb.flows.stock_perp_oracle_check
"""

from datetime import UTC, datetime, timedelta
from pathlib import Path

import obs
from prefect import flow, get_run_logger
from qa import checks, db, store

from venue_arb import validation
from venue_arb.config import PIPELINE_PREFIX
from venue_arb.sources import hyperliquid

PIPELINE = f"{PIPELINE_PREFIX}_stock_perp_oracle_check"
TABLE = validation.TABLE
COLUMNS = ["day", "symbol", "n", "median_abs_bps", "p95_abs_bps", "share_over_10", "open_gap_bps"]
DAYS = 5
CH_FROM, CH_TO = "{lo:DateTime64(3, 'UTC')}", "{hi:DateTime64(3, 'UTC')}"
CTX_SQL = f"SELECT symbol, ts, oracle FROM venue_arb.stock_perp_ctx WHERE ts >= {CH_FROM} AND ts < {CH_TO}"
REFS_SQL = ("SELECT symbol, ts, bid, ask, quote_time FROM venue_arb.stock_perp_refs "
            f"WHERE ref_kind = 'schwab' AND ts >= {CH_FROM} AND ts < {CH_TO}")
REPORT_DIR = Path("data/private")


def _utc(ts: datetime) -> datetime:
    return ts if ts.tzinfo else ts.replace(tzinfo=UTC)


def report(rows: list[dict], day) -> str:
    lines = [f"# Oracle vs Schwab, {day}", "",
             (f"Last {DAYS} ET days, worst p95 first. Private. Coverage: US-listed names only, external-session ticks "
              "with a fresh two-sided Schwab quote at the same tick, so in practice about 04:00-20:00 ET; the overnight "
              "external hours (20:00-04:00 ET) are not validated. The newest day is partial and is rewritten by the "
              "next run."), "",
             "| day | symbol | n | median bps | p95 bps | share >10 bps | Monday open gap bps |", "|---|---|---|---|---|---|---|"]
    for r in sorted(rows, key=lambda r: -r["p95_abs_bps"]):
        gap = "" if r["open_gap_bps"] is None else f"{r['open_gap_bps']:.1f}"
        lines.append(f"| {r['day']} | {r['symbol']} | {r['n']} | {r['median_abs_bps']:.1f} | {r['p95_abs_bps']:.1f} | "
                     f"{r['share_over_10']:.2f} | {gap} |")
    return "\n".join(lines) + "\n"


@flow(name=PIPELINE)
@obs.job("venue-arb.stock_perp_oracle_check")
def stock_perp_oracle_check_flow(end: datetime | None = None, pipeline: str = PIPELINE) -> int:
    log = get_run_logger()
    run_id = obs.current().id
    results: list[checks.Result] = []
    try:
        end = end or datetime.now(UTC)
        today = end.astimezone(validation.ET).date()
        first = today - timedelta(days=DAYS - 1)
        p = {"lo": datetime.combine(first, datetime.min.time(), tzinfo=validation.ET).astimezone(UTC),
             "hi": end.astimezone(UTC)}
        ch = db.ch()
        ctx = [{"symbol": s, "ts": _utc(t), "oracle": o} for s, t, o in ch.query(CTX_SQL, parameters=p).result_rows]
        refs = [{"symbol": s, "ts": _utc(t), "bid": b, "ask": a, "quote_time": _utc(q)}
                for s, t, b, a, q in ch.query(REFS_SQL, parameters=p).result_rows]
        rows = validation.compare(ctx, refs, hyperliquid.load_labels())
        if rows:
            ch.insert(TABLE, [[r[c] for c in COLUMNS] for r in rows], column_names=COLUMNS)
        results += validation.drift_results(checks, rows)
        REPORT_DIR.mkdir(parents=True, exist_ok=True)
        (REPORT_DIR / f"oracle-check-{today}.md").write_text(report(rows, today))
        results.append(checks.Result("oracle_check_rows", True, len(rows), severity="warn", target=TABLE))
        checks.enforce(results)
        log.info("oracle check ok: %d rows, %d drift warns", len(rows), len(results) - 1)
        return len(rows)
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
    stock_perp_oracle_check_flow()
