"""Order-book snapshot collector. Run: secrets uv run python -m venue_arb.collect

tick() and flush() are plain functions (fetchers, loader, recorder injected) so tests need no stack or network.
"""

import logging
import time
import uuid
from datetime import UTC, datetime

from venue_arb.books import to_rows, watchlist
from venue_arb.config import BOOK_DEPTH, PIPELINE_PREFIX, SNAPSHOT_INTERVAL_S, SQL_CHECKS
from venue_arb.sources import kalshi, polymarket

# qa is imported lazily in run_forever: unit tests (and CI) run without the platform checkout.

log = logging.getLogger("venue_arb.collect")
PIPELINE = f"{PIPELINE_PREFIX}_books"
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
    WHERE a_venue = c.a_venue AND a_id = c.a_id AND b_venue = c.b_venue AND b_id = c.b_id
    ORDER BY created_at DESC LIMIT 1
) adj ON true
"""


def default_fetchers() -> dict:
    return {
        "kalshi": lambda market_id: kalshi.parse_book(kalshi.fetch_book(market_id), BOOK_DEPTH),
        "polymarket": lambda token: polymarket.parse_book(polymarket.fetch_book(token), BOOK_DEPTH),
    }


def load_watchlist(conn) -> list[tuple[str, str, str | None]]:
    cur = conn.execute(WATCH_SQL)
    cols = [c.name for c in cur.description]
    return watchlist([dict(zip(cols, r, strict=True)) for r in cur.fetchall()])


def tick(watch, fetchers: dict, buf: dict[str, list[dict]], now: datetime, stats: dict[str, list[int]]) -> None:
    """Fetch every watched book into buf. stats[venue] = [attempts, errors]; failures are logged, never raised."""
    for venue, market_id, yes_token in watch:
        key = market_id if venue == "kalshi" else yes_token  # Kalshi by market id, Polymarket by yes token
        stats[venue][0] += 1
        try:
            if key is None:
                raise ValueError("no yes_token")
            buf[venue] += to_rows(venue, market_id, now, fetchers[venue](key))
        except Exception as e:  # noqa: BLE001 — one bad market must not stop the loop
            stats[venue][1] += 1
            log.warning("book fetch failed venue=%s market=%s error=%s", venue, market_id, type(e).__name__)


def flush(buf, venues, load, record, run_sql, checks, stats, max_buffer: int = MAX_BUFFER_ROWS) -> list:
    """Guard and load each venue independently, then SQL checks, then record everything. Never raises.

    `checks` is qa.checks (injected so this module imports without qa).
    """
    results = []
    for venue in sorted(venues):
        rows, target = buf[venue], f"venue_arb.book_levels:{venue}"
        attempts, errors = stats[venue]
        ratio = errors / attempts if attempts else 0.0
        results.append(checks.Result("fetch_errors", ratio <= MAX_FETCH_ERROR_RATIO, round(ratio, 4),
                                     MAX_FETCH_ERROR_RATIO, f"{errors}/{attempts} book fetches failed",
                                     target=target))
        stats[venue] = [0, 0]
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
    stats = {v: [0, 0] for v in VENUES}

    def load(rows):
        db.ch().insert("venue_arb.book_levels", [[r[c] for c in COLUMNS] for r in rows], column_names=COLUMNS)

    def record(results):
        with db.pg() as conn:
            store.record(conn, PIPELINE, str(uuid.uuid4()), results)

    while True:
        started = time.monotonic()
        try:
            if n % 10 == 0:
                with db.pg() as conn:
                    watch = load_watchlist(conn)
            n += 1
            tick(watch, fetchers, buf, datetime.now(UTC), stats)
            if n % flush_every == 0:
                flush(buf, {v for v, _, _ in watch}, load, record,
                      lambda: sql.run_all(pattern="book_levels_*.sql", directory=SQL_CHECKS), checks, stats)
        except Exception:
            log.exception("collector cycle failed")
        time.sleep(max(0.0, interval_s - (time.monotonic() - started)))


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    run_forever()
