import json
from dataclasses import replace
from datetime import UTC, datetime, timedelta

from venue_arb import catalog
from venue_arb.models import Listing

NOW = datetime(2026, 10, 19, 12, tzinfo=UTC)
COLUMNS = {
    "venue", "market_id", "event_id", "category", "title", "rules", "yes_label", "no_label", "yes_token",
    "no_token", "close_time", "resolution_source", "version", "raw", "last_seen",
}


def listing(market_id="m1", close=NOW + timedelta(days=3)):
    return Listing("kalshi", market_id, "e1", "fed_macro", "t", "r", "Yes", "No", None, None, close, None,
                   {"event": {"a": 1}, "market": {"b": 2}})


def test_to_rows_shapes_and_encodes_raw():
    ls = [listing()]
    rows = catalog.to_rows(ls, {("kalshi", "m1"): "v1"}, NOW)
    assert set(rows[0]) == COLUMNS
    assert isinstance(rows[0]["raw"], str) and json.loads(rows[0]["raw"]) == ls[0].raw
    assert rows[0]["version"] == "v1" and rows[0]["last_seen"] == NOW


def test_keep_current_drops_stale_close_times():
    fresh, edge, stale = listing("a"), listing("b", NOW - timedelta(hours=23)), listing("c", NOW - timedelta(days=2))
    assert catalog.keep_current([fresh, edge, stale], NOW) == [fresh, edge]
    boundary = replace(stale, close_time=NOW - timedelta(days=1))
    assert catalog.keep_current([boundary], NOW) == [boundary]
