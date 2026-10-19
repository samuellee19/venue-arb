"""A review counts only for the verdict it judged: a v1 review on a pair now judged v2 is ignored everywhere."""
import pytest

pytestmark = pytest.mark.integration


class _Rollback(Exception):
    pass


def test_v1_review_ignored_after_rejudge():
    from qa import db

    from venue_arb.flows import exact_sql, review_cli
    from venue_arb.matching.adjudicate import PROMPT_VERSION
    from venue_arb.report.note import SCOPE_SQL

    key = ("kalshi", "ZZTEST-K", "kv", "polymarket", "ZZTEST-P", "pv")
    with db.pg() as conn:
        with pytest.raises(_Rollback), conn.transaction():
            for venue, mid, ver in (("kalshi", "ZZTEST-K", "kv"), ("polymarket", "ZZTEST-P", "pv")):
                conn.execute("INSERT INTO venue_arb.listings (venue, market_id, title, version, close_time, raw) "
                             "VALUES (%s, %s, 't', %s, now() + interval '30 days', '{}')", (venue, mid, ver))
            conn.execute("INSERT INTO venue_arb.adjudications (a_venue,a_id,a_version,b_venue,b_id,b_version,"
                         "relation,yes_maps_to,confidence,prompt_version) VALUES (%s,%s,%s,%s,%s,%s,'exact','yes',"
                         "0.9,%s)", (*key, PROMPT_VERSION))
            conn.execute("INSERT INTO venue_arb.reviews (a_venue,a_id,a_version,b_venue,b_id,b_version,label,"
                         "prompt_version) VALUES (%s,%s,%s,%s,%s,%s,'wrong','v1')", key)

            def eligible_label():
                return [r["label"] for r in exact_sql.load_eligible(conn) if r["kalshi_id"] == "ZZTEST-K"]

            def scope_bad():
                return [r[3] for r in conn.execute(SCOPE_SQL, {"pv": PROMPT_VERSION}).fetchall()
                        if r[0] == "ZZTEST-K"]

            # the v1 'wrong' review judged a different verdict: not applied, not shown as done, not counted
            assert eligible_label() == [None] and scope_bad() == [False]
            assert key not in review_cli.load_labels(conn)
            n_all = len(review_cli.all_review_labels(conn))

            # reviewing the current verdict replaces the stale review and then counts everywhere
            review_cli.save_review(conn, dict(zip(review_cli.KEY, key, strict=True)) | {"prompt_version": PROMPT_VERSION},
                                   "wrong", "")
            assert eligible_label() == ["wrong"] and scope_bad() == [True]
            assert review_cli.load_labels(conn)[key] == "wrong"
            assert len(review_cli.all_review_labels(conn)) == n_all + 1
            raise _Rollback
        assert conn.execute("SELECT count(*) FROM venue_arb.listings WHERE market_id LIKE 'ZZTEST-%'").fetchone() == (0,)
