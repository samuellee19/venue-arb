"""Order-book snapshot collector. Run: secrets uv run python -m venue_arb.collect

tick() and flush() are plain functions (fetchers, loader, recorder injected) so tests need no stack or network.
"""

import logging
import time
import uuid
from datetime import UTC, datetime

from qa import checks, db, sql, store

from venue_arb.books import to_rows, watchlist
from venue_arb.config import BOOK_DEPTH, PIPELINE_PREFIX, SNAPSHOT_INTERVAL_S, SQL_CHECKS
from venue_arb.sources import kalshi, polymarket

log = logging.getLogger("venue_arb.collect")
PIPELINE = f"{PIPELINE_PREFIX}_books"
VENUES = ("kalshi", "polymarket")
CONTRACT = {"ts": "datetime", "venue": "str", "market_id": "str", "side": "str", "level": "int",
            "price": "float", "size": "float"}
COLUMNS = list(CONTRACT)
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


def tick(watch, fetchers: dict, buf: dict[str, list[dict]], now: datetime) -> dict[str, int]:
    """Fetch every watched book into buf. Per-market failures are logged and counted, never raised."""
    errors = dict.fromkeys(VENUES, 0)
    for venue, market_id, yes_token in watch:
        key = market_id if venue == "kalshi" else yes_token  # Kalshi by market id, Polymarket by yes token
        try:
            if key is None:
                raise ValueError("no yes_token")
            buf[venue] += to_rows(venue, market_id, now, fetchers[venue](key))
        except Exception as e:  # noqa: BLE001 — one bad market must not stop the loop
            errors[venue] += 1
            log.warning("book fetch failed venue=%s market=%s error=%s", venue, market_id, type(e).__name__)
    return errors


def flush(buf: dict[str, list[dict]], venues, load, record, run_sql) -> list[checks.Result]:
    """Guard and load each venue independently, then SQL checks, then record everything. Never raises on QA."""
    results: list[checks.Result] = []
    for venue in sorted(venues):
        rows, target = buf[venue], f"venue_arb.book_levels:{venue}"
        guards = [checks.expect_rows(len(rows), target), checks.expect_schema(rows, CONTRACT, target)]
        results += guards
        try:
            checks.enforce(guards)
            load(rows)
        except checks.DataQualityError as e:
            log.error("guard failed, dropping %s buffer: %s", venue, e)  # failures are already in results
        buf[venue] = []
    results += run_sql()
    record(results)
    return results


def run_forever(interval_s: int = SNAPSHOT_INTERVAL_S, flush_every: int = 5) -> None:
    fetchers, buf, watch, n = default_fetchers(), {v: [] for v in VENUES}, [], 0

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
            tick(watch, fetchers, buf, datetime.now(UTC))
            if n % flush_every == 0:
                flush(buf, {v for v, _, _ in watch}, load, record,
                      lambda: sql.run_all(pattern="book_levels_*.sql", directory=SQL_CHECKS))
        except Exception:
            log.exception("collector cycle failed")
        time.sleep(max(0.0, interval_s - (time.monotonic() - started)))


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    run_forever()
