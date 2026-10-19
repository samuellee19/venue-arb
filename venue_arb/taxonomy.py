"""Category assignment and listing version hash."""

import hashlib

from venue_arb.config import CATEGORIES
from venue_arb.models import Listing

# Checked 2026-10-19 against /trade-api/v2/events?series_ticker=T&status=open&limit=1: all
# listed tickers returned an open event, none dropped. *15M series excluded on purpose (noise).
KALSHI_SERIES = {
    "KXFEDDECISION": "fed_macro",
    "KXFED": "fed_macro",
    "KXCPI": "fed_macro",
    "KXGDP": "fed_macro",
    "KXRATECUTCOUNT": "fed_macro",
    "KXBTC": "crypto_price",
    "KXBTCD": "crypto_price",
    "KXETH": "crypto_price",
    "KXETHD": "crypto_price",
}

# Checked 2026-10-19 against gamma /events?tag_slug=S&active=true&closed=false&limit=1:
# every slug returned an event, none dropped.
POLYMARKET_TAGS = {
    "fed": "fed_macro",
    "fed-rates": "fed_macro",
    "economic-policy": "fed_macro",
    "inflation": "fed_macro",
    "bitcoin": "crypto_price",
    "ethereum": "crypto_price",
    "crypto-prices": "crypto_price",
}


def assign_category(
    listing: Listing, series_ticker: str | None, tag_slugs: list[str]
) -> str | None:
    if listing.venue == "kalshi":
        return KALSHI_SERIES.get(series_ticker or "")
    found = {POLYMARKET_TAGS[s] for s in tag_slugs if s in POLYMARKET_TAGS}
    return next((c for c in CATEGORIES if c in found), None)


def version(listing: Listing) -> str:
    parts = (
        listing.title,
        listing.rules,
        listing.yes_label,
        listing.close_time.isoformat(),
        listing.resolution_source or "",
    )
    return hashlib.sha256("\x1f".join(parts).encode()).hexdigest()[:16]
