"""Project constants."""

import os
from pathlib import Path

PIPELINE_PREFIX = "venue_arb"
CATEGORIES = ("fed_macro", "crypto_price")
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
