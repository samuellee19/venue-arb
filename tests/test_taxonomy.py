from dataclasses import replace
from datetime import UTC, datetime

from venue_arb.config import CATEGORIES
from venue_arb.models import Listing
from venue_arb.taxonomy import DISPLAY, assign_category, version

L = Listing(
    venue="kalshi", market_id="m", event_id="e", category=None, title="t", rules="r",
    yes_label="Yes", no_label="No", yes_token=None, no_token=None,
    close_time=datetime(2026, 1, 1, tzinfo=UTC), resolution_source=None, raw={},
)


def test_assign_category_kalshi_series():
    assert assign_category(L, "KXFEDDECISION", []) == "fed_macro"
    assert assign_category(L, "KXBTCD", []) == "crypto_price"
    assert assign_category(L, "KXBTC15M", []) is None


def test_assign_category_pm_tag_priority():
    assert assign_category(replace(L, venue="polymarket"), None, ["bitcoin", "fed"]) == "fed_macro"
    assert assign_category(replace(L, venue="polymarket"), None, ["sports"]) is None


def test_version_changes_on_rules_edit():
    assert version(L) == version(replace(L))
    assert version(L) != version(replace(L, rules="r2"))


def test_display_covers_every_category():
    assert set(CATEGORIES) <= set(DISPLAY) and all(DISPLAY[c] for c in CATEGORIES)
