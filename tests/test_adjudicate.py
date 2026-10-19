from datetime import date

import pytest
from pydantic import ValidationError

from venue_arb.matching.adjudicate import (
    PRICES,
    Verdict,
    adjudicate,
    daily_budget,
    month_spend,
    prompt_for,
    run,
    today_spend,
)

V = Verdict(
    relation="exact", yes_maps_to="yes", deadline_diff=None, threshold_diff=None,
    resolution_diff=None, reason="same event because both ask X, differs by nothing", confidence=0.95,
)


def listing(mid, version, venue="k"):
    return {
        "market_id": mid, "version": version, "title": f"title {mid}", "yes_label": "Yes", "no_label": "No",
        "rules": "rules", "close_time": "2026-10-28T18:00:00+00:00", "resolution_source": None,
    }


class Cur:
    def __init__(self, row):
        self.row = row

    def fetchone(self):
        return self.row


class FakeConn:
    def __init__(self, cached=()):
        self.cached = set(cached)  # (a_id, a_version, b_id, b_version)
        self.inserts = []

    def execute(self, sql, params=()):
        if sql.lstrip().upper().startswith("SELECT"):
            if "sum(cost_usd)" in sql:
                return Cur((1.5,))
            return Cur((1,) if tuple(params[i] for i in (1, 2, 4, 5)) in self.cached else None)
        self.inserts.append(params)
        return Cur(None)


class FakeCall:
    def __init__(self):
        self.n = 0

    def __call__(self, prompt):
        self.n += 1
        return V, 20_000, 0  # 20k input tokens at $0.10/M = $0.002


def test_cache_hit_skips_call():
    conn, call = FakeConn(cached=[("k1", "v1", "p1", "w1")]), FakeCall()
    out = run(conn, [(listing("k1", "v1"), listing("p1", "w1"))], 0.0, call=call)
    assert call.n == 0 and out["cached"] == 1 and out["adjudicated"] == 0 and conn.inserts == []


def test_cache_miss_on_new_version():
    conn, call = FakeConn(cached=[("k1", "v1", "p1", "w1")]), FakeCall()
    out = run(conn, [(listing("k1", "v1"), listing("p1", "w2"))], 0.0, call=call)
    assert call.n == 1 and out["adjudicated"] == 1 and len(conn.inserts) == 1
    assert out["spend"] == pytest.approx(0.002)


def test_budget_stops_adjudication():
    pairs = [(listing(f"k{i}", "v"), listing(f"p{i}", "w")) for i in range(3)]
    call = FakeCall()
    out = run(FakeConn(), pairs, 19.999, call=call)
    assert call.n == 0 and out["adjudicated"] == 0 and out["skipped_budget"] == 3


def test_verdict_rejects_bad_relation():
    with pytest.raises(ValidationError):
        Verdict(relation="maybe", yes_maps_to="yes", deadline_diff=None, threshold_diff=None,
                resolution_diff=None, reason="x", confidence=0.5)


def test_adjudicate_cost_and_prompt():
    v, cost = adjudicate(listing("k", "v"), listing("p", "w"), call=FakeCall())
    assert v == V and cost == pytest.approx(20_000 * PRICES[next(iter(PRICES))][0] / 1e6)
    assert "same resolution source" in prompt_for(listing("k", "v"), listing("p", "w"))


def test_month_spend():
    assert month_spend(FakeConn()) == 1.5


def test_error_isolated_and_counted():
    n = [0]

    def flaky(prompt):
        n[0] += 1
        if n[0] == 1:
            Verdict.model_validate({"relation": "maybe"})
        return V, 1000, 0

    pairs = [(listing(f"k{i}", "v"), listing(f"p{i}", "w")) for i in range(2)]
    out = run(FakeConn(), pairs, 0.0, call=flaky)
    assert out["adjudicated"] == 1 and out["errors"] == 1


def test_verdict_rejects_bad_confidence():
    with pytest.raises(ValidationError):
        Verdict(relation="exact", yes_maps_to="yes", reason="x", confidence=1.5)


def test_today_spend():
    assert today_spend(FakeConn()) == 1.5


def test_daily_budget_even_split():
    assert daily_budget(0, 0, date(2026, 10, 19)) == pytest.approx(20 / 13)


def test_daily_budget_nearly_spent_month():
    assert daily_budget(19.99, 0, date(2026, 10, 19)) == pytest.approx(0.01 / 13)


def test_daily_budget_zero_once_allowance_used():
    assert daily_budget(1.54, 1.54, date(2026, 10, 19)) == 0.0  # allowance (20-1.54+1.54)/13 = 1.538 < 1.54
    assert daily_budget(25, 0, date(2026, 10, 19)) == 0.0


def test_daily_budget_last_day_takes_all_remaining():
    assert daily_budget(15, 1, date(2026, 10, 31)) == pytest.approx(5.0)  # 20 - 14 spent before today - 1 spent today
