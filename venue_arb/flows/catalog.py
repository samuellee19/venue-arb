"""Catalog flow: Kalshi + Polymarket listings -> venue_arb.listings through qa.

    secrets uv run python -m venue_arb.flows.catalog
"""

import logging
import uuid
from dataclasses import replace
from datetime import UTC, datetime, timedelta

from prefect import flow, get_run_logger, task
from prefect.runtime import flow_run
from qa import checks, db, sql, store

from venue_arb import catalog
from venue_arb.config import PIPELINE_PREFIX, SQL_CHECKS
from venue_arb.models import Listing
from venue_arb.sources import kalshi, polymarket
from venue_arb.taxonomy import KALSHI_SERIES, POLYMARKET_TAGS, assign_category, version

PIPELINE = f"{PIPELINE_PREFIX}_catalog"
TARGET = "venue_arb.listings"
CONTRACT = {"venue": "str", "market_id": "str", "event_id": "str", "category": "str", "title": "str", "rules": "str",
            "yes_label": "str", "no_label": "str", "yes_token": "str", "no_token": "str", "close_time": "datetime",
            "resolution_source": "str", "version": "str", "raw": "str", "last_seen": "datetime"}
NULLABLE = frozenset({"yes_token", "no_token", "resolution_source"})
logging.getLogger("httpx").setLevel(logging.WARNING)


@task(retries=2)
def fetch(venue: str) -> list[Listing]:
    if venue == "kalshi":
        return kalshi.parse_listings(kalshi.list_events(list(KALSHI_SERIES)))
    return polymarket.parse_listings(polymarket.list_events(list(POLYMARKET_TAGS)))


def categorize(listings: list[Listing]) -> list[Listing]:
    """Assign categories, drop uncategorized, de-duplicate on market_id (a Polymarket market sits in many tags)."""
    out = {}
    for x in listings:
        ev = x.raw["event"]
        cat = assign_category(x, ev.get("series_ticker"), [t["slug"] for t in ev.get("tags", [])])
        if cat:
            out.setdefault((x.venue, x.market_id), replace(x, category=cat))
    return list(out.values())


@flow(name=PIPELINE)
def catalog_flow() -> dict:
    log = get_run_logger()
    run_id = str(flow_run.get_id() or uuid.uuid4())
    results: list[checks.Result] = []
    try:
        now = datetime.now(UTC)
        by_venue = {v: catalog.keep_current(categorize(fetch(v)), now) for v in ("kalshi", "polymarket")}
        listings = [x for xs in by_venue.values() for x in xs]
        rows = catalog.to_rows(listings, {(x.venue, x.market_id): version(x) for x in listings}, now)
        pre = [checks.expect_rows(len(xs), f"{TARGET}:{v}") for v, xs in by_venue.items()]
        pre.append(checks.expect_schema(rows, CONTRACT, TARGET, nullable=NULLABLE))
        results += pre
        checks.enforce(pre)
        with db.pg() as conn:
            n = catalog.upsert(conn, rows)
            seen, latest = conn.execute("SELECT count(*), max(last_seen) FROM venue_arb.listings WHERE last_seen = %s",
                                        (now,)).fetchone()
        results += [checks.expect_count_match(n, seen, TARGET), checks.expect_fresh(latest, TARGET, timedelta(hours=1))]
        results += sql.run_all(pattern="listings_*.sql", directory=SQL_CHECKS)
        checks.enforce(results)
        log.info("catalog ok: %s", {v: len(xs) for v, xs in by_venue.items()})
        return {v: len(xs) for v, xs in by_venue.items()}
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
    catalog_flow()
