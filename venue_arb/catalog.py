"""Catalog rows: shaping and upsert (pure; the connection is injected, qa is never imported)."""

import json
from datetime import datetime, timedelta

from venue_arb.models import Listing

COLUMNS = ["venue", "market_id", "event_id", "category", "title", "rules", "yes_label", "no_label", "yes_token",
           "no_token", "close_time", "resolution_source", "version", "raw", "last_seen"]
STALE_AFTER = timedelta(days=1)

UPSERT = (
    f"INSERT INTO venue_arb.listings ({', '.join(COLUMNS)}) VALUES ({', '.join(f'%({c})s' for c in COLUMNS)})"
    " ON CONFLICT (venue, market_id) DO UPDATE SET "
    + ", ".join(f"{c} = EXCLUDED.{c}" for c in COLUMNS if c not in ("venue", "market_id"))
)


def keep_current(listings: list[Listing], now: datetime) -> list[Listing]:
    """Polymarket keeps events 'open' months past endDate; drop anything closed more than a day ago."""
    return [x for x in listings if x.close_time >= now - STALE_AFTER]


def to_rows(listings: list[Listing], versions: dict[tuple[str, str], str], now: datetime) -> list[dict]:
    return [
        {**{c: getattr(x, c) for c in COLUMNS if c not in ("version", "raw", "last_seen")},
         "version": versions[(x.venue, x.market_id)], "raw": json.dumps(x.raw), "last_seen": now}
        for x in listings
    ]


def upsert(conn, rows: list[dict]) -> int:
    if rows:
        with conn.cursor() as cur:
            cur.executemany(UPSERT.replace("%(raw)s", "%(raw)s::jsonb"), rows)
    return len(rows)
