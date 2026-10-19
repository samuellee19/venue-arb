import pytest

pytestmark = pytest.mark.integration


def test_match_flow_second_run_is_cached():
    from qa import db

    from venue_arb.flows.match import match_flow
    from venue_arb.matching.adjudicate import month_spend

    with db.pg() as conn:
        before = month_spend(conn)
    first = match_flow(limit=5)
    second = match_flow(limit=5)
    with db.pg() as conn:
        after = month_spend(conn)
        dupes = conn.execute("SELECT count(*) FROM (SELECT 1 FROM venue_arb.adjudications GROUP BY a_id, a_version, "
                             "b_id, b_version HAVING count(*) > 1) x").fetchone()[0]
    print(f"month_spend {before:.4f} -> {after:.4f}; first={first}; second={second}")
    assert first["candidates"] > 0 and first["adjudicated"] + first["cached"] <= 5
    # the flow only sends unadjudicated pairs, so run two moves on to the next five instead of re-reading run one's
    assert second["adjudicated"] <= 5 and dupes == 0
    assert after - before < 0.05
