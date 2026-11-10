"""Project constants."""

import os
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

PIPELINE_PREFIX = "venue_arb"
# the public repository the page footer, wordmark and research-note links point at; set it here (or VENUE_ARB_REPO_URL on
# the server) once the GitHub repository exists. The server injects it into index.html; the static files carry no URL.
REPO_URL = os.environ.get("VENUE_ARB_REPO_URL", "https://github.com/samuellee19/venue-arb")
CATEGORIES = ("fed_macro", "crypto_price", "stock_perps")
SQL_CHECKS = Path("sql_checks")
CONFIDENCE_FLOOR = 0.8
# LLM spend per API credit cycle (from CREDIT_DAY, when the monthly API credit arrives); a runaway guard
MONTHLY_LLM_BUDGET_USD = float(os.environ.get("VENUE_ARB_LLM_BUDGET_USD", "30"))
CREDIT_DAY = int(os.environ.get("PLATFORM_CREDIT_DAY", "20"))  # 1-28: every month has that day


def cycle_start(now: datetime | None = None, day: int = CREDIT_DAY) -> datetime:
    """Midnight UTC on the latest credit day on or before `now`: budgets count from here, not from the 1st."""
    now = now or datetime.now(UTC)
    d = now.replace(day=day, hour=0, minute=0, second=0, microsecond=0)
    return d if d <= now else (d.replace(day=1) - timedelta(days=1)).replace(day=day)


def next_credit(now: datetime | None = None, day: int = CREDIT_DAY) -> date:
    return (cycle_start(now, day).replace(day=28) + timedelta(days=4)).replace(day=day).date()
LLM_MODEL = os.environ.get("VENUE_ARB_LLM_MODEL", "claude-haiku-5-5")
SNAPSHOT_INTERVAL_S = 60
SNAPSHOT_MAX_MARKETS = 300
BOOK_DEPTH = 10
LADDER_CAP = 60  # snapshot strikes per same-instant ladder, nearest to the Polymarket range midpoint
LADDER_PAD_DEFAULT = 50.0  # USD clip pad when an asset has < LADDER_MIN_BASIS basis rows
LADDER_MIN_BASIS = 24
BASIS_WINDOW_DAYS = 30  # trailing basis window for the ladder bounds (collector pad, ladder_score, the note)
LADDER_WINDOW_HOURS = 3  # ladder_score rescoring window
DASHBOARD_SHOW_BOOKS = False  # public order-book quotes: both venues' terms must allow it; docs/venues.md "Public data terms" (Sam to review)
DASHBOARD_SHOW_STOCK_PERPS = True  # on since 2026-10-25 (Sam); turns on the public oracle view (perp vs the trade.xyz oracle, Hyperliquid data only); terms read 2026-10-23, docs/going-public.md has the remaining gates
STOCK_PERP_MIN_NET_BPS = 10.0  # net edge floor after round-trip costs and funding; the executable mode also charges borrow, the oracle mode does not
STOCK_PERP_HOLD_H = 24  # assumed holding period for the funding and borrow charge
STOCK_PERP_HOLD_SENSITIVITY_H = (1, 168)  # holding periods the edge is re-scored at
STOCK_PERP_SENSITIVITY_DAYS = 14  # trailing window the research block re-finds episodes over (memory-bounded)
STOCK_PERP_BORROW_BPS_YR = 50.0  # stock borrow cost, bps per year
REF_MAX_AGE_S = 120  # a stock reference quote older than this is not comparable to the perp book
