import pytest
from pydantic import ValidationError

from venue_arb.matching.adjudicate import PRICES, Verdict, adjudicate, month_spend, prompt_for, run

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
