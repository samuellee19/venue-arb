from datetime import date

import anthropic
import httpx
import pytest
from pydantic import ValidationError

from venue_arb.matching.adjudicate import (
    EST_CALL_USD,
    MAX_CONSECUTIVE_ERRORS,
    PRICES,
    PROMPT_VERSION,
    Verdict,
    adjudicate,
    daily_budget,
    month_spend,
    prompt_for,
    run,
    run_checks,
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
    def __init__(self, cached=(), errors=(), old=(), error_type="X"):
        self.cached = set(cached)  # (a_id, a_version, b_id, b_version) stored at the current prompt version
        self.old = set(old)  # same, stored at prompt v1
        self.sqls = []
        self.inserts = []  # adjudications rows
        self.attempts = [{"pair": k, "outcome": "error", "error_type": error_type, "cost": 0.0} for k in errors]

    def execute(self, sql, params=()):
        pair = tuple(params[i] for i in (1, 2, 4, 5)) if params else None
        if "sum(cost_usd)" in sql:
            assert "venue_arb.llm_attempts" in sql  # spend comes from the ledger, not adjudications
            return Cur((1.5,))
        if "count(*) FROM venue_arb.llm_attempts" in sql:
            transient = params[-1] if len(params) > 6 else ()
            return Cur((sum(a["pair"] == pair and a["outcome"] == "error" and a["error_type"] not in transient
                            for a in self.attempts),))
        if sql.startswith("SELECT 1 FROM venue_arb.adjudications"):
            hit = pair in self.cached and params[6] == PROMPT_VERSION or pair in self.old and params[6] == "v1"
            return Cur((1,) if hit else None)
        if sql.startswith("INSERT INTO venue_arb.llm_attempts"):
            self.attempts.append({"pair": pair, "outcome": params[7], "error_type": params[8], "cost": params[9]})
        else:
            self.inserts.append(params)
            self.sqls.append(sql)
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
    assert "same authority" in prompt_for(listing("k", "v"), listing("p", "w"))


def test_month_spend():
    assert month_spend(FakeConn()) == 1.5


def test_error_isolated_and_counted():
    n = [0]

    def flaky(prompt):
        n[0] += 1
        return ({"relation": "maybe"} if n[0] == 1 else V), 1000, 0

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


def test_ok_attempt_recorded_in_ledger():
    conn = FakeConn()
    run(conn, [(listing("k1", "v"), listing("p1", "w"))], 0.0, call=FakeCall())
    assert [(a["outcome"], a["cost"]) for a in conn.attempts] == [("ok", pytest.approx(0.002))]


def test_api_error_attempt_recorded_with_estimate():
    def down(prompt):
        raise anthropic.APIConnectionError(request=httpx.Request("POST", "https://api.anthropic.com"))

    conn = FakeConn()
    out = run(conn, [(listing("k1", "v"), listing("p1", "w"))], 0.0, call=down)
    assert out["errors"] == 1 and conn.inserts == []
    assert conn.attempts == [{"pair": ("k1", "v", "p1", "w"), "outcome": "error",
                              "error_type": "APIConnectionError", "cost": EST_CALL_USD}]


def test_validation_failure_records_real_cost():
    conn = FakeConn()
    out = run(conn, [(listing("k1", "v"), listing("p1", "w"))], 0.0,
              call=lambda prompt: ({"relation": "maybe"}, 5_000, 0))  # billed: 5k tokens = $0.0005
    assert out["errors"] == 1 and out["spend"] == pytest.approx(0.0005)
    assert [(a["outcome"], a["error_type"], a["cost"]) for a in conn.attempts] == [
        ("error", "ValidationError", pytest.approx(0.0005))]


def test_no_tool_call_records_real_cost():
    conn = FakeConn()
    run(conn, [(listing("k1", "v"), listing("p1", "w"))], 0.0, call=lambda prompt: (None, 5_000, 0))
    assert conn.attempts[0]["outcome"] == "error" and conn.attempts[0]["cost"] == pytest.approx(0.0005)


def test_pair_skipped_after_three_errors():
    conn, call = FakeConn(errors=[("k1", "v", "p1", "w")] * 3), FakeCall()
    pairs = [(listing("k1", "v"), listing("p1", "w")), (listing("k1", "v2"), listing("p1", "w"))]
    out = run(conn, pairs, 0.0, call=call)
    assert out["skipped_failing"] == 1 and call.n == 1 and out["adjudicated"] == 1  # new version is retried


def test_consecutive_errors_stop_the_run():
    pairs = [(listing(f"k{i}", "v"), listing(f"p{i}", "w")) for i in range(10)]
    out = run(FakeConn(), pairs, 0.0, call=lambda prompt: (None, 0, 0))
    assert out["errors"] == MAX_CONSECUTIVE_ERRORS


def base_out(**kw):
    return {"adjudicated": 0, "cached": 0, "skipped_budget": 0, "skipped_failing": 0, "errors": 0, "spend": 0.0} | kw


def by_check(out):
    return {r["check"]: r for r in run_checks(out)}


def test_run_checks_all_pass():
    rs = by_check(base_out(adjudicated=5))
    assert set(rs) == {"adjudication_errors", "skipped_budget", "skipped_failing"}
    assert all(r["passed"] for r in rs.values())


def test_run_checks_error_ratio():
    severe = by_check(base_out(adjudicated=2, errors=3))["adjudication_errors"]
    assert not severe["passed"] and severe["severity"] == "error" and severe["observed"] == 0.6
    few = by_check(base_out(adjudicated=0, errors=4))["adjudication_errors"]  # attempted < 5
    assert not few["passed"] and few["severity"] == "warn"
    half = by_check(base_out(adjudicated=5, errors=5))["adjudication_errors"]  # ratio 0.5, not > 0.5
    assert half["severity"] == "warn"


def test_run_checks_skips_warn():
    rs = by_check(base_out(skipped_budget=3, skipped_failing=1))
    assert [(rs[k]["passed"], rs[k]["severity"], rs[k]["observed"]) for k in ("skipped_budget", "skipped_failing")] \
        == [(False, "warn", 3), (False, "warn", 1)]


def test_cache_respects_prompt_version():
    pair = [(listing("k1", "v1"), listing("p1", "w1"))]
    call = FakeCall()
    run(FakeConn(old=[("k1", "v1", "p1", "w1")]), pair, 0.0, call=call)
    assert call.n == 1  # stored v1 row: re-judged
    call = FakeCall()
    out = run(FakeConn(cached=[("k1", "v1", "p1", "w1")]), pair, 0.0, call=call)
    assert call.n == 0 and out["cached"] == 1


def test_rejudge_upserts():
    conn = FakeConn(old=[("k1", "v1", "p1", "w1")])
    v = V.model_copy(update={"tail_risks": "Meeting cancelled resolves No Change."})
    run(conn, [(listing("k1", "v1"), listing("p1", "w1"))], 0.0, call=lambda p: (v.model_dump(), 1000, 0))
    assert "ON CONFLICT (a_venue,a_id,a_version,b_venue,b_id,b_version) DO UPDATE SET" in conn.sqls[0]
    assert conn.inserts[0][-2:] == ("v3", "Meeting cancelled resolves No Change.")


def test_prompt_v2_mentions_rules():
    p = prompt_for(listing("k", "v"), listing("p", "w"))
    assert "Trading closes" in p and "decision date" in p and "tail_risks" in p and "untrusted" in p


def test_verdict_tail_risks_optional():
    assert V.tail_risks is None
    assert Verdict(relation="exact", yes_maps_to="yes", reason="x", confidence=0.9, tail_risks="a").tail_risks == "a"


def test_tail_risks_normalized():
    def tr(x):
        return Verdict(relation="exact", yes_maps_to="yes", reason="x", confidence=0.9, tail_risks=x).tail_risks

    assert tr(["a", "b"]) == "a; b"
    assert tr('["a", "b"]') == "a; b"
    assert tr("plain") == "plain"
    assert tr([]) is None and tr("") is None and tr("[]") is None


def test_transient_errors_do_not_cap_pair():
    pair = [(listing("k1", "v"), listing("p1", "w"))]
    e = [("k1", "v", "p1", "w")] * 3
    out = run(FakeConn(errors=e, error_type="RateLimitError"), pair, 0.0, call=FakeCall())
    assert out["adjudicated"] == 1 and out["skipped_failing"] == 0
    out = run(FakeConn(errors=e, error_type="BilledError"), pair, 0.0, call=FakeCall())
    assert out["adjudicated"] == 0 and out["skipped_failing"] == 1


def test_prompt_v3_mentions_bracket_rule():
    p = prompt_for(listing("k", "v"), listing("p", "w"))
    assert "mutually exclusive" in p and "multiples of 25" in p
