"""Weekly top-up of venue_arb.stock_perp_candles: hourly candles and funding of the last 4 days for every labelled perp,
Monday 14:00 UTC, an hour before stock_perp_weekend, whose fallback reads these candles when the 60 s contexts do not
reach back to Friday evening. Candles only (no Schwab). Fetch and insert are the backfill's (load_candles).

    secrets uv run python -m venue_arb.flows.stock_perp_candles [--days N] [--dry-run] [SYMBOL ...]

Request budget (1,200 weight per minute per IP; see stock_perp_backfill): 4 days is 96 hourly items, so one symbol
costs a candleSnapshot (20 + 2) and at most two fundingHistory calls (20 + 5, then an empty page at 20): 67 weight.
The 111 labels cost about 7,400, over six minutes of the whole IP budget, which venue-arb-perps also spends (about
270 a minute). So load_paced takes 700 // 67 = 10 symbols a batch (670 weight) with 60 s between batches: 12 batches,
about 13 minutes. A wider --days gets smaller batches (60 days: 196 weight a symbol, 3 a batch, about 40 minutes).

Idempotent: the table is a ReplacingMergeTree ordered by (symbol, hour), so a re-run of a window rewrites the same
keys and FINAL reads one row per hour. Only complete hours are written.
"""

import argparse
import sys
from datetime import UTC, datetime, timedelta

import obs
from prefect import flow, get_run_logger
from qa import checks, db, store

from venue_arb.config import PIPELINE_PREFIX
from venue_arb.flows import stock_perp_backfill as bf
from venue_arb.sources import hyperliquid

PIPELINE = f"{PIPELINE_PREFIX}_stock_perp_candles"
DAYS = 4


def window(now: datetime, days: int = DAYS) -> tuple[datetime, datetime]:
    """[start, end) on whole hours: end is the start of the current hour, so the open hour is left out."""
    end = now.astimezone(UTC).replace(minute=0, second=0, microsecond=0)
    return end - timedelta(days=days), end


def _symbols(only: list[str] | None) -> list[str]:
    labels = hyperliquid.load_labels()
    unknown = sorted(set(only or ()) - labels.keys())
    if unknown:
        raise ValueError(f"not in labels: {', '.join(unknown)}")
    return list(only or labels)


@flow(name=PIPELINE)
@obs.job("venue-arb.stock_perp_candles")
def stock_perp_candles_flow(end: datetime | None = None, days: int = DAYS, symbols: list[str] | None = None,
                            pipeline: str = PIPELINE) -> dict:
    log = get_run_logger()
    run_id = obs.current().id
    results: list[checks.Result] = []
    try:
        start, stop = window(end or datetime.now(UTC), days)
        todo = _symbols(symbols)
        with obs.step("load", symbols=len(todo), days=days):
            loaded, failed = bf.load_paced(db.ch(), todo, start, stop, log.warning)
        rows = sum(loaded.values())
        results.append(checks.Result("candle_symbols_failed", not failed, len(failed), 0,
                                     detail=", ".join(failed)[:500], target=bf.CANDLES))
        results.append(checks.Result("candle_rows", True, rows, severity="warn", target=bf.CANDLES))
        checks.enforce(results)
        log.info("stock perp candles ok: %d rows, %d symbols, %s to %s", rows, len(loaded), start, stop)
        return {"rows": rows, "symbols": len(loaded)}
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


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(prog="python -m venue_arb.flows.stock_perp_candles")
    ap.add_argument("symbols", nargs="*", help="labels to load (default: all)")
    ap.add_argument("--days", type=int, default=DAYS)
    ap.add_argument("--dry-run", action="store_true", help="print the window, batches and weight; no venue, no DB")
    a = ap.parse_args(argv)
    if not a.dry_run:
        stock_perp_candles_flow(days=a.days, symbols=a.symbols or None)
        return 0
    start, end = window(datetime.now(UTC), a.days)
    todo = _symbols(a.symbols or None)
    w = bf.request_weight(a.days * 24)
    batch = max(1, bf.PER_MINUTE // w)
    print(f"window {start:%Y-%m-%d %H:%M} to {end:%Y-%m-%d %H:%M} UTC, {len(todo)} symbols, "
          f"{w} weight each ({w * len(todo)} total), {batch} a batch, {-(-len(todo) // batch)} batches "
          f"{bf.PAUSE_S:.0f} s apart")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
