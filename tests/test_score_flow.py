from datetime import UTC, datetime, timedelta

import pytest

pytestmark = pytest.mark.integration


def counts():
    from qa import db

    (edges,) = db.ch().query("SELECT count() FROM venue_arb.edges FINAL").result_rows[0]
    with db.pg() as conn:
        (opps,) = conn.execute("SELECT count(*) FROM venue_arb.opportunities").fetchone()
    return edges, opps


def test_score_flow_idempotent():
    from venue_arb.flows.score import score_flow

    # end pinned 10 min back: the collector flushes every ~5 min, so the window's books are settled
    end = datetime.now(UTC).replace(second=0, microsecond=0) - timedelta(minutes=10)
    first = score_flow(window_hours=2, end=end)  # raises if any error check fails
    after_first = counts()
    second = score_flow(window_hours=2, end=end)
    print(f"first={first} second={second} counts={after_first}")
    assert first == second and first["pairs"] > 0 and first["edge_rows"] > 0
    assert counts() == after_first
