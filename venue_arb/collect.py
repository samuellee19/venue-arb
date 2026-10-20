"""Order-book snapshot collector. Run: secrets uv run python -m venue_arb.collect

tick() and flush() are plain functions (fetchers, loader, recorder injected) so tests need no stack or network.
"""

import logging
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from functools import partial

from venue_arb import basis, http
from venue_arb.books import to_rows, watchlist
from venue_arb.config import (
    BOOK_DEPTH,
    LADDER_MIN_BASIS,
    LADDER_PAD_DEFAULT,
    PIPELINE_PREFIX,
    SNAPSHOT_INTERVAL_S,
    SQL_CHECKS,
)
from venue_arb.matching import crypto_rule
from venue_arb.sources import kalshi, polymarket

# qa is imported lazily in run_forever: unit tests (and CI) run without the platform checkout.

log = logging.getLogger("venue_arb.collect")
logging.getLogger("httpx").setLevel(logging.WARNING)
PIPELINE = f"{PIPELINE_PREFIX}_books"
TABLE = "venue_arb.book_levels_v2"  # partitioned by day, 90-day TTL (db/migrations/ch/002_books_partition.sql)
WORKERS = 8
VENUES = ("kalshi", "polymarket")
CONTRACT = {"ts": "datetime", "venue": "str", "market_id": "str", "side": "str", "level": "int",
            "price": "float", "size": "float"}
COLUMNS = list(CONTRACT)
MAX_FETCH_ERROR_RATIO = 0.2
MAX_BUFFER_ROWS = 200_000  # per venue; oldest rows dropped while the loader is failing
WATCH_SQL = """
SELECT c.a_venue, c.a_id, la.yes_token AS a_token, c.b_venue, c.b_id, lb.yes_token AS b_token,
       adj.relation, c.score
FROM venue_arb.candidates c
JOIN venue_arb.listings la ON la.venue = c.a_venue AND la.market_id = c.a_id
JOIN venue_arb.listings lb ON lb.venue = c.b_venue AND lb.market_id = c.b_id
LEFT JOIN LATERAL (
    SELECT relation FROM venue_arb.adjudications
    WHERE a_venue = c.a_venue AND a_id = c.a_id AND a_version = la.version
      AND b_venue = c.b_venue AND b_id = c.b_id AND b_version = lb.version
    ORDER BY created_at DESC LIMIT 1
) adj ON true
WHERE la.last_seen > now() - INTERVAL '2 hours' AND lb.last_seen > now() - INTERVAL '2 hours'
  AND la.close_time > now() AND lb.close_time > now()
"""


def default_fetchers() -> dict:
    get = partial(http.get_json, delay=0)  # no polite delay for book calls (retries kept); WORKERS bounds the rate
    return {
        "kalshi": lambda market_id: kalshi.parse_book(kalshi.fetch_book(market_id, get=get), BOOK_DEPTH),
        "polymarket": lambda token: polymarket.parse_book(polymarket.fetch_book(token, get=get), BOOK_DEPTH),
    }


LISTING_COLS = ("venue", "market_id", "event_id", "category", "title", "yes_label", "close_time", "yes_token")
LISTINGS_SQL = (f"SELECT {', '.join(LISTING_COLS)} FROM venue_arb.listings WHERE category = 'crypto_price' "
                "AND last_seen > now() - INTERVAL '2 hours' AND close_time > now()")
BASIS_SQL = "SELECT asset, diff FROM venue_arb.basis WHERE instant > now() - INTERVAL 30 DAY"


def ladder_pads(basis_rows) -> dict[str, float]:
    """Clip pad per asset: max(up, down) of the 30-day basis bounds; LADDER_PAD_DEFAULT with < LADDER_MIN_BASIS rows."""
    diffs: dict[str, list[float]] = {}
    for asset, diff in basis_rows:
        diffs.setdefault(asset, []).append(diff)
    return {a: max(basis.bounds(d)) if len(d) >= LADDER_MIN_BASIS else LADDER_PAD_DEFAULT for a, d in diffs.items()}


def load_ladders(conn, ch) -> list[dict]:
    """Same-instant crypto ladders from current listings, Kalshi side clipped to Polymarket's range +- the basis pad."""
    cur = conn.execute(LISTINGS_SQL)
    found = crypto_rule.ladders([dict(zip(LISTING_COLS, r, strict=True)) for r in cur.fetchall()])
    try:
        pads = ladder_pads(ch.query(BASIS_SQL).result_rows)
    except Exception as e:  # noqa: BLE001 — no basis history: fall back to the default pad
        log.warning("basis read failed, default ladder pad error=%s", type(e).__name__)
        pads = {}
    return [crypto_rule.clip(lad, pads.get(asset, LADDER_PAD_DEFAULT)) for (asset, _), lad in found.items()]


def load_watchlist(conn, ch=None) -> list[tuple[str, str, str | None]]:
    cur = conn.execute(WATCH_SQL)
    cols = [c.name for c in cur.description]
    rows = [dict(zip(cols, r, strict=True)) for r in cur.fetchall()]
    ladders = []
    if ch is not None:
        try:
            ladders = load_ladders(conn, ch)
        except Exception as e:  # noqa: BLE001 — ladders are additive; the Fed pairs must keep flowing
            log.warning("ladder load failed, watchlist without ladders error=%s", type(e).__name__)
    return watchlist(rows, ladders)


def _fetch(fetchers: dict, venue: str, market_id: str, yes_token: str | None):
    """(rows, None) or (None, error type). Each book is stamped when its response arrives."""
    try:
        key = market_id if venue == "kalshi" else yes_token  # Kalshi by market id, Polymarket by yes token
        if key is None:
            raise ValueError("no yes_token")
        levels = fetchers[venue](key)
        return to_rows(venue, market_id, datetime.now(UTC), levels), None
    except Exception as e:  # noqa: BLE001 — one bad market must not stop the loop
        return None, type(e).__name__


def tick(watch, fetchers: dict, buf: dict[str, list[dict]], stats: dict[str, list[int]],
         workers: int = WORKERS) -> None:
    """Fetch every watched book into buf, `workers` at a time. stats[venue] = [attempts, errors, empty books];
    failures are logged and counted, never raised. Accounting happens on this thread, so it needs no lock."""
    with ThreadPoolExecutor(max_workers=workers) as pool:
        results = pool.map(lambda w: _fetch(fetchers, *w), watch)
        for (venue, market_id, _), (rows, err) in zip(watch, results, strict=True):
            stats[venue][0] += 1
            if err:
                stats[venue][1] += 1
                log.warning("book fetch failed venue=%s market=%s error=%s", venue, market_id, err)
            elif not rows:
                stats[venue][2] += 1
            else:
                buf[venue] += rows


def flush(buf, venues, load, record, run_sql, checks, stats, max_buffer: int = MAX_BUFFER_ROWS,
          cycles: list[float] = (), interval_s: float = SNAPSHOT_INTERVAL_S) -> list:
    """Guard and load each venue independently, then SQL checks, then record everything.

    Load and guard failures are recorded, not raised; an error from run_sql or record propagates to the caller.
    `checks` is qa.checks (injected so this module imports without qa). `cycles` are tick durations in seconds.
    """
    results = []
    if cycles:
        worst = max(cycles)
        results.append(checks.Result("cycle_seconds", worst <= interval_s, round(worst, 1), interval_s,
                                     f"slowest of {len(cycles)} cycles took {worst:.1f}s", severity="warn",
                                     target=TABLE))
    for venue in sorted(venues):
        rows, target = buf[venue], f"{TABLE}:{venue}"
        attempts, errors, empty = stats[venue]
        ratio = errors / attempts if attempts else 0.0
        results.append(checks.Result("fetch_errors", ratio <= MAX_FETCH_ERROR_RATIO, round(ratio, 4),
                                     MAX_FETCH_ERROR_RATIO, f"{errors}/{attempts} book fetches failed",
                                     target=target))
        results.append(checks.Result("empty_books", empty == 0, empty, 0,
                                     f"{empty}/{attempts} books came back with no levels", severity="warn",
                                     target=target))
        stats[venue] = [0, 0, 0]
        guards = [checks.expect_rows(len(rows), target), checks.expect_schema(rows, CONTRACT, target)]
        results += guards
        if any(not g.passed and g.severity == "error" for g in guards):
            log.error("guard failed, dropping %s buffer", venue)
            buf[venue] = []
            continue
        try:
            load(rows)
            buf[venue] = []
        except Exception as e:  # noqa: BLE001 — recorded below; rows kept for an idempotent retry
            log.error("load failed venue=%s error=%s", venue, type(e).__name__)
            results.append(checks.Result("load", False, None, None, f"load raised {type(e).__name__}",
                                         target=target))
            buf[venue] = rows[-max_buffer:]
    results += run_sql()
    record(results)
    return results


def run_forever(interval_s: int = SNAPSHOT_INTERVAL_S, flush_every: int = 5) -> None:
    from qa import checks, db, sql, store

    fetchers, buf, watch, n = default_fetchers(), {v: [] for v in VENUES}, [], 0
    stats, cycles = {v: [0, 0, 0] for v in VENUES}, []

    def load(rows):
        db.ch().insert(TABLE, [[r[c] for c in COLUMNS] for r in rows], column_names=COLUMNS)

    def record(results):
        with db.pg() as conn:
            store.record(conn, PIPELINE, str(uuid.uuid4()), results)

    while True:
        started = time.monotonic()
        try:
            if n % 10 == 0:
                with db.pg() as conn:
                    watch = load_watchlist(conn, db.ch())
            n += 1
            tick(watch, fetchers, buf, stats)
            cycles.append(time.monotonic() - started)
            if n % flush_every == 0:
                flush(buf, {v for v, _, _ in watch}, load, record,
                      lambda: sql.run_all(pattern="book_levels_*.sql", directory=SQL_CHECKS), checks, stats,
                      cycles=cycles, interval_s=interval_s)
                cycles.clear()
        except Exception:
            log.exception("collector cycle failed")
        time.sleep(max(0.0, interval_s - (time.monotonic() - started)))


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    run_forever()
