"""Deterministic judges tried before the LLM, in order. Each module has RULE_MODEL and decide(a, b) -> Verdict | None."""

from venue_arb.matching import crypto_rule, fed_rule
from venue_arb.matching.adjudicate import Verdict

RULES = (fed_rule, crypto_rule)
RULE_MODELS = frozenset(r.RULE_MODEL for r in RULES)


def decide(a: dict, b: dict) -> tuple[str, Verdict] | None:
    """(rule model, verdict) from the first rule that decides the pair, else None."""
    for r in RULES:
        if (v := r.decide(a, b)) is not None:
            return r.RULE_MODEL, v
    return None
