import pytest

import venue_arb.matching.fed_rule as fr
from venue_arb.matching.fed_rule import RULE_MODEL, decide, kalshi_outcome, polymarket_outcome

UP = frozenset(range(50, 501, 25))
DOWN = frozenset(-x for x in UP)


def k(leg, ym="26DEC"):
    return {"market_id": f"KXFEDDECISION-{ym}-{leg}", "title": "Will the Federal Reserve ..."}


def pm(what, month="December 2026"):
    return {"market_id": "0xabc", "title": what.format(month=month)}


INC25 = "Will the Fed increase interest rates by 25 bps after the {month} meeting?"
INC50 = "Will the Fed increase interest rates by 50+ bps after the {month} meeting?"
DEC25 = "Will the Fed decrease interest rates by 25 bps after the {month} meeting?"
DEC50 = "Will the Fed decrease interest rates by 50+ bps after the {month} meeting?"
HOLD = "Will there be no change in Fed interest rates after the {month} meeting?"


@pytest.mark.parametrize("leg,out", [
    ("H0", {0}), ("H25", {25}), ("H26", UP), ("C25", {-25}), ("C26", DOWN)])
def test_kalshi_legs(leg, out):
    assert kalshi_outcome(k(leg, "27JAN")) == ("2027-01", frozenset(out))


def test_kalshi_unknown_or_non_fed():
    assert kalshi_outcome(k("X9")) is None
    assert kalshi_outcome({"market_id": "KXBTC-26DEC-T1", "title": "x"}) is None


@pytest.mark.parametrize("title,out", [
    (INC25, {25}), (INC50, UP), (DEC25, {-25}), (DEC50, DOWN), (HOLD, {0})])
def test_polymarket_variants(title, out):
    assert polymarket_outcome(pm(title, "October 2026")) == ("2026-10", frozenset(out))


def test_polymarket_non_bracket_is_none():
    assert polymarket_outcome(pm("Hike 25 bps flips No change for October Fed by October 27?")) is None
    assert polymarket_outcome(pm("Will BTC hit 100k?")) is None


def test_exact_pairs():
    v = decide(k("H25"), pm(INC25))
    assert v.relation == "exact" and v.yes_maps_to == "yes" and v.confidence == 1.0 and v.tail_risks
    assert decide(k("H26"), pm(INC50)).relation == "exact"
    assert decide(k("H0"), pm(HOLD)).relation == "exact"
    assert decide(k("C25"), pm(DEC25)).relation == "exact"


@pytest.mark.parametrize("leg,title", [
    ("H26", INC25), ("H0", INC25), ("C26", DEC25), ("H25", INC50), ("C25", INC25)])
def test_different_brackets(leg, title):
    v = decide(k(leg), pm(title))
    assert v.relation == "different" and v.tail_risks is None


def test_different_meeting():
    assert decide(k("H25", "26OCT"), pm(INC25)).relation == "different"


def test_overlap_is_near():
    assert fr._relation(frozenset({25, 50}), UP) == "near"


@pytest.mark.parametrize("title", [
    "Will the Fed increase interest rates by 75 bps after the December 2026 meeting?",
    "Will the Fed increase interest rates by 25 bps or more after the December 2026 meeting?",
    "Will the Fed increase interest rates by 25 bps after the December 2026 meeting? (updated)",
    "Will the Fed increase interest rates by 25 bps after the Smarch 2026 meeting?"])
def test_parser_negatives(title):
    assert polymarket_outcome({"market_id": "x", "title": title}) is None


def test_non_fed_row_is_none():
    assert decide({"market_id": "KXBTC-1", "title": "x"}, pm(INC25)) is None
    assert decide(k("H25"), pm("Will BTC hit 100k?")) is None
    assert RULE_MODEL == "rule:fed-v1"
