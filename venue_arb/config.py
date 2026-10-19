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
