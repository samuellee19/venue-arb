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


class _Rollback(Exception):
    pass


def test_upsert_opportunities_and_overlap_check():
    from pathlib import Path

    from qa import db

    from venue_arb import scoring
    from venue_arb.episodes import Episode
    from venue_arb.flows.score import upsert_opportunities

    overlap_sql = (Path(__file__).parent.parent / "sql_checks" / "opportunities_no_overlap.sql").read_text()
    t = datetime(2026, 1, 1, tzinfo=UTC)

    def opp(start, end):
        return scoring.opportunity_row(Episode("ZZTEST-K", "ZZTEST-P", "k_yes_pm_no", start, end, 2, 0.05, 285.0, 1.5))

    def mine(conn, sql):
        return [r for r in conn.execute(sql).fetchall() if r[0] == "ZZTEST-K"]

    with db.pg() as conn:
        with pytest.raises(_Rollback), conn.transaction():
            assert upsert_opportunities(conn, [opp(t, t + timedelta(minutes=1))]) == 1
            assert upsert_opportunities(conn, [opp(t, t + timedelta(minutes=5))]) == 1  # same key, later end
            rows = conn.execute("SELECT end_ts FROM venue_arb.opportunities WHERE kalshi_id = 'ZZTEST-K'").fetchall()
            assert rows == [(t + timedelta(minutes=5),)]
            assert mine(conn, overlap_sql) == []
            upsert_opportunities(conn, [opp(t + timedelta(minutes=2), t + timedelta(minutes=8))])
            assert len(mine(conn, overlap_sql)) == 1
            raise _Rollback
        (left,) = conn.execute("SELECT count(*) FROM venue_arb.opportunities WHERE kalshi_id = 'ZZTEST-K'").fetchone()
        assert left == 0
