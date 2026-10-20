"""Deterministic judge for Fed rate-decision bracket markets (pure; no qa, no LLM).

Verified 2026-10-19 against live listings (venue_arb.listings):
- Kalshi `KXFEDDECISION-<YY><MON>-<LEG>`: yes_label and no_label are identical text ("Hike 25bps"), so the leg is read
  from the ticker alone (not from the labels). H0 = "Hike rates by 0bps" (Fed maintains rate), H25 = hike exactly 25,
  H26 = "Hike >25bps", C25 = cut exactly 25, C26 = "Cut >25bps". Legs are mutually exclusive.
- Polymarket titles (yes_label "Yes"/no_label "No"; token 0 is the bracket's Yes): "Will the Fed increase|decrease
  interest rates by 25|50+ bps after the <Month YYYY> meeting?" and "Will there be no change in Fed interest rates
  after the <Month YYYY> meeting?". Other Fed titles (e.g. "Hike 25 bps flips No change ...") are not brackets -> None.
- Every Fed target change since Oct 1989 is a multiple of 25 bp, so ">25" and "50+" are the same set on the grid.
"""

import re
from calendar import month_name

from venue_arb.matching.adjudicate import Verdict

RULE_MODEL = "rule:fed-v1"
_MAX = 500
_UP = frozenset(range(50, _MAX + 1, 25))
_DOWN = frozenset(-x for x in _UP)
_LEGS = {"H0": {0}, "H25": {25}, "H26": _UP, "C25": {-25}, "C26": _DOWN}
_MON = {m.upper()[:3]: i for i, m in enumerate(month_name) if m}
_MONTHS = {m.lower(): i for i, m in enumerate(month_name) if m}
_K = re.compile(r"^KXFEDDECISION-(\d\d)([A-Z]{3})-([A-Z]\d+)$")
_PM = re.compile(r"^Will the Fed (increase|decrease) interest rates by (25|50\+) bps after the (\w+) (\d{4}) meeting\?$")
_PM0 = re.compile(r"^Will there be no change in Fed interest rates after the (\w+) (\d{4}) meeting\?$")
TAIL = ("An off-grid (non-25 bp) move — none since Oct 1989 — could resolve differently: Polymarket rounds odd moves up "
        "to the next bracket, Kalshi thresholds are strict; A cancelled or rescheduled meeting resolves under each "
        "venue's own fallback rules")

Outcome = tuple[str, frozenset[int]]


def kalshi_outcome(row: dict) -> Outcome | None:
    m = _K.match(row["market_id"])
    if not m or m[3] not in _LEGS or m[2] not in _MON:
        return None
    return f"20{m[1]}-{_MON[m[2]]:02d}", frozenset(_LEGS[m[3]])


def polymarket_outcome(row: dict) -> Outcome | None:
    t = row["title"].strip()
    if m := _PM.match(t):
        sign = 1 if m[1] == "increase" else -1
        moves = {sign * 25} if m[2] == "25" else {sign * x for x in _UP}
        mon, year = m[3], m[4]
    elif m := _PM0.match(t):
        moves, mon, year = {0}, m[1], m[2]
    else:
        return None
    if mon.lower() not in _MONTHS:
        return None
    return f"{year}-{_MONTHS[mon.lower()]:02d}", frozenset(moves)


def _relation(a: frozenset, b: frozenset) -> str:
    return "exact" if a == b else "near" if a & b else "different"


def _fmt(s: frozenset) -> str:
    if len(s) == 1:
        return f"{next(iter(s)):+d} bp"
    return f"{min(s):+d} to {max(s):+d} bp" if max(s) < 0 or min(s) > 0 else str(sorted(s))


def decide(a: dict, b: dict) -> Verdict | None:
    """Verdict for a Kalshi (a) / Polymarket (b) pair, or None unless both are Fed decision brackets."""
    ka, pb = kalshi_outcome(a), polymarket_outcome(b)
    if not (ka and pb):
        return None
    rel = "different" if ka[0] != pb[0] else _relation(ka[1], pb[1])
    where = "the same meeting" if ka[0] == pb[0] else f"different meetings ({ka[0]} vs {pb[0]})"
    return Verdict(
        relation=rel, yes_maps_to="yes", confidence=1.0,
        reason=f"Rule: Kalshi Yes is a move of {_fmt(ka[1])}, Polymarket Yes is {_fmt(pb[1])}, at {where}: {rel}.",
        tail_risks=TAIL if rel != "different" else None)
