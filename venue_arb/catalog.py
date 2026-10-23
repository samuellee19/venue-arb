"""Catalog rows: shaping and upsert (pure; the connection is injected, qa is never imported)."""

import json
from dataclasses import replace
from datetime import datetime, timedelta

from venue_arb.models import Listing
from venue_arb.taxonomy import assign_category

COLUMNS = ["venue", "market_id", "event_id", "category", "title", "rules", "yes_label", "no_label", "yes_token",
           "no_token", "close_time", "resolution_source", "version", "raw", "last_seen"]
STALE_AFTER = timedelta(days=1)

UPSERT = (
    f"INSERT INTO venue_arb.listings ({', '.join(COLUMNS)}) VALUES ({', '.join(f'%({c})s' for c in COLUMNS)})"
    " ON CONFLICT (venue, market_id) DO UPDATE SET "
    + ", ".join(f"{c} = EXCLUDED.{c}" for c in COLUMNS if c not in ("venue", "market_id"))
)


def categorize(listings: list[Listing]) -> list[Listing]:
    """Assign categories, drop uncategorized, de-duplicate on market_id (a Polymarket market sits in many tags)."""
    out = {}
    for x in listings:
        ev = x.raw["event"]
        slugs = [t.get("slug") for t in ev.get("tags") or [] if isinstance(t, dict)]
        cat = assign_category(x, ev.get("series_ticker"), slugs)
        if cat:
            out.setdefault((x.venue, x.market_id), replace(x, category=cat))
    return list(out.values())


def keep_current(listings: list[Listing], now: datetime) -> list[Listing]:
    """Polymarket keeps events 'open' months past endDate; drop anything closed more than a day ago."""
    return [x for x in listings if x.close_time >= now - STALE_AFTER]


def to_rows(listings: list[Listing], versions: dict[tuple[str, str], str], now: datetime) -> list[dict]:
    return [
        {**{c: getattr(x, c) for c in COLUMNS if c not in ("version", "raw", "last_seen")},
         "version": versions[(x.venue, x.market_id)], "raw": json.dumps(x.raw), "last_seen": now}
        for x in listings
    ]


def listings_stamp(conn):
    """max(last_seen): the catalog flow upserts every listing in one transaction stamped with its run time, so this
    changes once per run, after the commit. The collector and the publisher reload when it moves, so the hourly crypto
    ladders that open at :00 reach the page minutes after the catalog lists them, not up to two reload periods later."""
    return conn.execute("SELECT max(last_seen) FROM venue_arb.listings").fetchone()[0]


def upsert(conn, rows: list[dict]) -> int:
    if rows:
        with conn.cursor() as cur:
            cur.executemany(UPSERT.replace("%(raw)s", "%(raw)s::jsonb"), rows)
    return len(rows)
