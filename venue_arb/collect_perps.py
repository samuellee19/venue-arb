"""Stock-perp snapshot collector. Run: secrets uv run python -m venue_arb.collect_perps

Every tick: one Hyperliquid contexts call per labelled deployer (trade.xyz whole, other deployers' labelled names
only), one book per perp (thread pool), one Schwab quotes call for the perps with a stock reference. tick() and flush() are plain functions (fetchers, loader and recorder injected) so tests need no stack
or network; qa and obs are imported lazily in run_forever.
"""

import logging
import os
import time
import uuid
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from functools import partial

from venue_arb import collect
from venue_arb.config import PIPELINE_PREFIX, SNAPSHOT_INTERVAL_S, SQL_CHECKS
from venue_arb.sources import hyperliquid, schwab
from venue_arb.sources.hyperliquid import PerpCtx, catalog, label_or_none

log = logging.getLogger("venue_arb.collect_perps")
logging.getLogger("httpx").setLevel(logging.WARNING)
PIPELINE = f"{PIPELINE_PREFIX}_perps"
TABLES = {"books": "venue_arb.stock_perp_books", "ctx": "venue_arb.stock_perp_ctx", "refs": "venue_arb.stock_perp_refs"}
CONTRACTS = {
    "books": {"ts": "datetime", "symbol": "str", "side": "str", "level": "int", "price": "float", "size": "float"},
    "ctx": {"ts": "datetime", "symbol": "str", "mark": "float", "oracle": "float", "mid": "float",
            "funding_h": "float", "premium": "float", "open_interest": "float", "day_volume_usd": "float",
            "growth_mode": "int"},
    "refs": {"ts": "datetime", "symbol": "str", "ref_kind": "str", "ref_symbol": "str", "bid": "float",
             "ask": "float", "bid_size": "float", "ask_size": "float", "last": "float", "quote_time": "datetime"},
}
NULLABLE = {"books": frozenset(), "ctx": frozenset({"mid", "premium"}), "refs": frozenset({"last"})}
ALERT_TOKEN_LEFT = timedelta(hours=48)  # error severity: the platform alerter pushes it to Telegram; two days to run the login


def _book_rows(fetch_book, now: datetime, symbol: str):
    """(rows, None) or (None, error type); rows carry the tick time, shared with the ctx and refs rows."""
    try:
        _, levels = fetch_book(symbol)  # the venue's book time is dropped: the table has no column for it
        return [{"ts": now, "symbol": symbol, "side": lv.side, "level": lv.level, "price": lv.price, "size": lv.size}
                for lv in levels], None
    except Exception as e:  # noqa: BLE001 — one bad book must not stop the loop
        return None, type(e).__name__


def _ctx_row(c: PerpCtx, now: datetime) -> dict:
    return {"ts": now, "symbol": c.symbol, "mark": c.mark, "oracle": c.oracle, "mid": c.mid, "funding_h": c.funding_h,
            "premium": c.premium, "open_interest": c.open_interest, "day_volume_usd": c.day_volume_usd,
            "growth_mode": int(c.growth_mode)}


def _snapshot_refs(perps, labels, quotes, buf, stats, now) -> None:
    pairs = [(c.symbol, lab) for c in perps if (lab := label_or_none(c.symbol, labels)).ref_kind == "schwab"]
    if quotes is None or not pairs:
        return
    try:
        got = quotes(list(dict.fromkeys(lab.ref_symbol for _, lab in pairs)))
    except schwab.SchwabAuthError:
        if not stats["schwab_auth"]:  # once per flush window
            log.error("schwab login expired, stock leg stopped: re-run the login CLI")
        stats["schwab_auth"] += 1
        return
    except Exception as e:  # noqa: BLE001 — a Schwab fault is the stock leg's warn (flush_results), not a refs error
        stats["schwab_fetch_errors"] += 1
        log.warning("schwab quotes failed error=%s", type(e).__name__)
        return
    stats["refs"][0] += 1
    for symbol, lab in pairs:
        q = got.get(lab.ref_symbol)
        if q is None:
            stats["refs"][2] += 1
            continue
        buf["refs"].append({"ts": now, "symbol": symbol, "ref_kind": lab.ref_kind, "ref_symbol": lab.ref_symbol,
                            "bid": q.bid, "ask": q.ask, "bid_size": q.bid_size, "ask_size": q.ask_size,
                            "last": q.last, "quote_time": q.quote_time})


def tick(perps: list[PerpCtx] | None, labels: dict, fetch_ctxs, fetch_book, quotes, buf: dict[str, list[dict]],
         stats: dict, now: datetime, workers: int = 8) -> list[PerpCtx]:
    """Snapshot contexts, books and stock quotes into buf. `perps` is the previous catalog, reused when the contexts
    call fails; `quotes` is None when Schwab is not configured. stats[table] = [attempts, errors, empty]; a failed
    Schwab call counts in stats["schwab_fetch_errors"] instead (warn severity: the perp side does not depend on it);
    failures are logged and counted, never raised. Returns the catalog for the next tick."""
    stats["ctx"][0] += 1
    try:
        ctxs = fetch_ctxs()
    except Exception as e:  # noqa: BLE001
        stats["ctx"][1] += 1
        log.warning("contexts fetch failed error=%s", type(e).__name__)
    else:
        kept, unlabelled = catalog(ctxs, labels)
        by_symbol = {c.symbol: c for c in ctxs}
        perps = kept + [by_symbol[s] for s in unlabelled]
        stats["unlabelled"] = unlabelled
        buf["ctx"] += [_ctx_row(c, now) for c in perps]
    perps = perps or []
    with ThreadPoolExecutor(max_workers=workers) as pool:
        results = pool.map(partial(_book_rows, fetch_book, now), [c.symbol for c in perps])
        for c, (rows, err) in zip(perps, results, strict=True):
            stats["books"][0] += 1
            if err:
                stats["books"][1] += 1
                log.warning("book fetch failed symbol=%s error=%s", c.symbol, err)
            elif not rows:
                stats["books"][2] += 1
            else:
                buf["books"] += rows
    _snapshot_refs(perps, labels, quotes, buf, stats, now)
    return perps


def flush_results(checks, stats: dict, issued_at: datetime | None, now: datetime) -> list:
    """Checks on the stock leg and the label files (the perp side keeps working without Schwab). The two Schwab
    credential checks are error severity so the platform alerter pushes them (once, then "recovered"); the collector
    never enforces them, so a failed one does not stop the flush."""
    results = []
    if issued_at is None:
        results.append(checks.Result("schwab_auth", False, None, None, "SCHWAB_REFRESH_TOKEN not set",
                                     severity="error", target=PIPELINE))
    else:
        n = stats["schwab_auth"]
        results.append(checks.Result("schwab_auth", n == 0, n, 0, f"{n} Schwab auth failures since the last flush",
                                     severity="error", target=PIPELINE))
        left = schwab.refresh_expires_at(issued_at) - now
        results.append(checks.Result("schwab_token_expires", left >= ALERT_TOKEN_LEFT,
                                     round(left.total_seconds() / 3600, 1), ALERT_TOKEN_LEFT.total_seconds() / 3600,
                                     f"Schwab refresh token expires in {left}; re-run the login CLI",
                                     severity="error", target=PIPELINE))
    n = stats["schwab_fetch_errors"]
    results.append(checks.Result("schwab_fetch_errors", n == 0, n, 0, f"{n} Schwab quote calls failed since the last flush",
                                 severity="warn", target=PIPELINE))
    unlabelled = stats["unlabelled"]
    results.append(checks.Result("unlabelled_perps", not unlabelled, len(unlabelled), 0,
                                 f"listed but in neither label file: {', '.join(unlabelled) or 'none'}",
                                 severity="warn", target=PIPELINE))
    return results


def flush(buf, loader_for, record, run_sql, checks, stats, issued_at, now, cycles: list[float] = (),
          interval_s: float = SNAPSHOT_INTERVAL_S) -> list:
    """collect.flush once per table (`loader_for(key)` returns that table's loader), then the stock-leg checks, SQL
    checks, record. A table nothing was tried for (e.g. Schwab unset) is skipped unless it holds retry rows. The refs
    table's expect_rows is a warn: no quote back is a Schwab fault, and the perp side keeps its error-severity checks."""
    results = []
    for key, table in TABLES.items():
        if not stats[key][0] and not buf[key]:
            continue
        out = collect.flush(buf, {key}, loader_for(key), lambda r: None, list, checks, stats, cycles=cycles,
                            interval_s=interval_s, table=table, contract=CONTRACTS[key], nullable=NULLABLE[key])
        if key == "refs":
            out = [replace(r, severity="warn") if r.check == "expect_rows" else r for r in out]
        results += out
        cycles = ()
    results += flush_results(checks, stats, issued_at, now)
    stats["schwab_auth"] = stats["schwab_fetch_errors"] = 0
    results += run_sql()
    record(results)
    return results


def _schwab_from_env(env=os.environ):
    """(quotes callable | None, refresh token issue time | None). The stock leg needs all four variables."""
    names = ("SCHWAB_CLIENT_ID", "SCHWAB_CLIENT_SECRET", "SCHWAB_REFRESH_TOKEN", "SCHWAB_REFRESH_ISSUED_AT")
    if not all(env.get(n) for n in names):
        return None, None
    client = schwab.Client(env[names[0]], env[names[1]], env[names[2]])
    return client.quotes, datetime.fromisoformat(env[names[3]])


def reload_labels(last: tuple[dict, set], load: Callable[[], dict], load_excluded: Callable[[], set]) -> tuple[dict, set]:
    """(labels, excluded), re-read; on a failed read the last good pair (a bad edit must not stall the collector).
    With nothing good yet (`last` labels empty) the error propagates: the cycle fails and is retried."""
    try:
        return load(), load_excluded()
    except Exception as e:
        if not last[0]:
            raise
        log.error("label reload failed error=%s, keeping the last good labels", type(e).__name__)
        return last


def run_forever(interval_s: int = SNAPSHOT_INTERVAL_S, flush_every: int = 5) -> None:
    import obs
    from qa import checks, db, sql, store

    obs.init(service="venue-arb")
    quotes, issued_at = _schwab_from_env()
    post = partial(hyperliquid.post_json, delay=0)  # no polite delay: the worker count bounds the rate
    buf, n, perps, labels, excluded, cycles = {k: [] for k in TABLES}, 0, None, {}, set(), []
    stats = {**{k: [0, 0, 0] for k in TABLES}, "schwab_auth": 0, "schwab_fetch_errors": 0, "unlabelled": []}

    def fetch_ctxs():
        return [c for c in hyperliquid.fetch_ctxs_for(labels, post=post) if c.symbol not in excluded]

    def loader_for(key):
        cols = list(CONTRACTS[key])
        return lambda rows: db.ch().insert(TABLES[key], [[r[c] for c in cols] for r in rows], column_names=cols)

    def record(results):
        with db.pg() as conn:
            store.record(conn, PIPELINE, obs.current().id if obs.current() else str(uuid.uuid4()), results)

    while True:
        started = time.monotonic()
        try:
            if n % 10 == 0:
                labels, excluded = reload_labels((labels, excluded), hyperliquid.load_labels, hyperliquid.load_excluded)
            n += 1
            perps = tick(perps, labels, fetch_ctxs, partial(hyperliquid.fetch_book, post=post), quotes, buf, stats,
                         datetime.now(UTC))
            cycles.append(time.monotonic() - started)
            if n % flush_every == 0:
                with obs.run("venue-arb.perps"):
                    flush(buf, loader_for, record, lambda: sql.run_all(pattern="stock_perp_books_fresh.sql", directory=SQL_CHECKS),
                          checks, stats, issued_at, datetime.now(UTC), cycles, interval_s)
                cycles.clear()
        except Exception:
            log.exception("perp collector cycle failed")
        time.sleep(max(0.0, interval_s - (time.monotonic() - started)))


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    run_forever()
