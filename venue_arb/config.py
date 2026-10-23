"""Project constants."""

import os
from pathlib import Path

PIPELINE_PREFIX = "venue_arb"
CATEGORIES = ("fed_macro", "crypto_price", "stock_perps")
SQL_CHECKS = Path("sql_checks")
CONFIDENCE_FLOOR = 0.8
MONTHLY_LLM_BUDGET_USD = 20.0
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
DASHBOARD_SHOW_STOCK_PERPS = False  # turns on the public oracle view (perp vs the trade.xyz oracle, Hyperliquid data only); terms read 2026-10-23, docs/going-public.md has the remaining gates
STOCK_PERP_MIN_NET_BPS = 10.0  # net edge floor after round-trip costs and funding; the executable mode also charges borrow, the oracle mode does not
STOCK_PERP_HOLD_H = 24  # assumed holding period for the funding and borrow charge
STOCK_PERP_HOLD_SENSITIVITY_H = (1, 168)  # holding periods the edge is re-scored at
STOCK_PERP_SENSITIVITY_DAYS = 14  # trailing window the research block re-finds episodes over (memory-bounded)
STOCK_PERP_BORROW_BPS_YR = 50.0  # stock borrow cost, bps per year
REF_MAX_AGE_S = 120  # a stock reference quote older than this is not comparable to the perp book
