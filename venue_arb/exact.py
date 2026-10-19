"""Exact Kalshi<->Polymarket matches eligible for arbitrage scoring (pure)."""
from dataclasses import dataclass
from datetime import datetime
from typing import Literal

MIN_CONFIDENCE = 0.8


@dataclass(frozen=True)
class ExactPair:
    kalshi_id: str
    pm_id: str
    pm_yes_token: str
    yes_maps_to: Literal["yes", "no"]
    close_time: datetime
    kalshi_series: str
    pm_fee_schedule: dict | None
    pm_fees_enabled: bool
    confidence: float
    reviewed: bool


def eligible(rows: list[dict]) -> list[ExactPair]:
    out = []
    for r in rows:
        if r["relation"] != "exact" or r["label"] in ("wrong", "unsure"):
            continue
        if r["label"] != "correct" and (r["confidence"] or 0) < MIN_CONFIDENCE:
            continue
        out.append(ExactPair(
            r["kalshi_id"], r["pm_id"], r["pm_yes_token"], r["yes_maps_to"],
            max(r["kalshi_close"], r["pm_close"]), r["kalshi_series"],
            r["pm_fee_schedule"], bool(r["pm_fees_enabled"]), r["confidence"], r["label"] == "correct",
        ))
    return out
