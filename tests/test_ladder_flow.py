from datetime import UTC, datetime, timedelta

import pytest

from venue_arb import ladder_edges as le
from venue_arb.models import Level

NOFEE = {"fee_type": "quadratic", "fee_multiplier": 0}


def test_no_edges_at_or_after_settlement():
    instant = datetime(2026, 10, 20, 8, tzinfo=UTC)
    ts = [instant + timedelta(seconds=s) for s in (-120, -60, -1, 0, 60)]
    k = {t: [Level("yes", 0, 0.40, 100.0)] for t in ts}
    pm = {t + timedelta(seconds=2): [Level("no", 0, 0.50, 100.0)] for t in ts}  # PM a little after Kalshi
    pts = le.spread_points("k_yes_pm_no", k, pm, instant, NOFEE, None, False)
    # -1 s Kalshi aligns with a PM snapshot at +1 s: after settlement, dropped
    assert [t for t, _ in pts] == ts[:2]
    assert all(e.direction == "k_yes_pm_no" and e.capacity_contracts == 100.0 for _, e in pts)


def test_score_ladder_rows_kinds_and_episodes():
    from venue_arb.matching.crypto_rule import Strike

    instant = datetime(2026, 10, 20, 8, tzinfo=UTC)

    def strike(venue, mid, level):
        return Strike(venue, mid, None, "ETH", instant, "gt", level)

    ladder = {"kalshi": [strike("kalshi", "KA", 79_999.99), strike("kalshi", "KB", 80_499.99),
                         strike("kalshi", "KC", 81_000.0)],  # KC has no books
              "polymarket": [strike("polymarket", "PA", 80_000.0), strike("polymarket", "PB", 80_200.0)]}
    asks = {("kalshi", "KA"): (0.40, 0.62), ("kalshi", "KB"): (0.30, 0.75),
            ("polymarket", "PA"): (0.60, 0.45), ("polymarket", "PB"): (0.55, 0.50)}  # (yes ask, no ask)
    ts = [instant - timedelta(minutes=m) for m in (3, 2, 1, 0)]
    snaps = {key: {t: [Level("no", 0, n, 500.0), Level("yes", 0, y, 500.0)] for t in ts}
             for key, (y, n) in asks.items()}
    rows, eps, stats = le.score_ladder("ETH", instant, ladder, snaps, 6.0, 7.0, lambda _: NOFEE,
                                       lambda _: (None, False), "v")
    # admissible: KA/PB k_yes_pm_no, KB/PA and KB/PB pm_yes_k_no (KA/PA is near-exact) x 3 snapshots before the instant
    assert stats == {"kalshi_strikes_booked": 2, "pm_strikes_booked": 2, "pairs_scored": 4, "admissible_checks": 9}
    got = {(r["k_id"], r["pm_id"], r["direction"], r["kind"]) for r in rows}
    assert got == {("KA", "PA", "k_yes_pm_no", "near_exact"), ("KA", "PA", "pm_yes_k_no", "near_exact"),
                   ("KA", "PB", "k_yes_pm_no", "arb"), ("KB", "PA", "pm_yes_k_no", "arb"),
                   ("KB", "PB", "pm_yes_k_no", "arb")}
    assert len(rows) == 5 * 3 and all(r["ts"] < instant for r in rows)  # the snapshot at the instant is dropped
    [ep] = eps  # KA Yes 0.40 + PB No 0.50: $0.10 a contract, $450 deep, 3 snapshots
    assert (ep.kalshi_id, ep.pm_id, ep.direction, ep.snapshots) == ("KA", "PB", "k_yes_pm_no", 3)
    assert ep.max_edge == pytest.approx(0.10)
    # near-exact KA/PA k_yes_pm_no also clears $0.01 (0.40 + 0.45) but is never an episode
    assert any(r["kind"] == "near_exact" and r["capacity_contracts"] > 0 for r in rows)


# Synthetic ladder for the integration test. Ids start with ZZTEST-; the instant (Mar 15, 2027 noon ET) has no real
# ladder, and last_seen is 3 h old so the collector, matcher and exact-pair readers (last_seen > now() - 2 h) skip them.
INSTANT = datetime(2027, 3, 15, 16, tzinfo=UTC)
K_ID, PM_NEAR, PM_ARB = "ZZTEST-KXBTCD-T79999.99", "ZZTEST-PM-80000", "ZZTEST-PM-80200"


def _seed(end):
    import json

    from qa import db

    def pm(mid, x):
        return ("polymarket", mid, None, "crypto_price", f"Will the price of Bitcoin be above ${x:,} on March 15?",
                "Yes", INSTANT, mid + "-yes", json.dumps({"market": {"feeSchedule": None, "feesEnabled": False}}))

    listings = [("kalshi", K_ID, "KXBTCD-ZZTEST", "crypto_price", "ZZTEST", "$80,000 or above", INSTANT, None,
                 json.dumps({"event": {"series_ticker": "KXBTCD"}})), pm(PM_NEAR, 80_000), pm(PM_ARB, 80_200)]
    with db.pg() as conn:
        conn.cursor().executemany(
            "INSERT INTO venue_arb.listings (venue, market_id, event_id, category, title, yes_label, close_time, "
            "yes_token, raw, first_seen, last_seen) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, "
            "now() - interval '3 hours', now() - interval '3 hours')", listings)
    # Kalshi Yes 0.40 + PM(80,200) No 0.50, 500 deep, every minute for 6 minutes: one k_yes_pm_no episode
    asks = {("kalshi", K_ID): (0.40, 0.62), ("polymarket", PM_NEAR): (0.60, 0.45), ("polymarket", PM_ARB): (0.55, 0.50)}
    rows = [[end - timedelta(minutes=m, seconds=5 * (v == "polymarket")), v, mid, side, 0, price, 500.0]
            for m in range(1, 7) for (v, mid), (y, n) in asks.items() for side, price in (("yes", y), ("no", n))]
    db.ch().insert("venue_arb.book_levels_v2", rows,
                   column_names=["ts", "venue", "market_id", "side", "level", "price", "size"])


def _cleanup():
    from qa import db

    ch = db.ch()
    ch.command("DELETE FROM venue_arb.book_levels_v2 WHERE market_id LIKE 'ZZTEST-%'")
    ch.command("DELETE FROM venue_arb.ladder_edges WHERE k_id LIKE 'ZZTEST-%'")
    with db.pg() as conn:
        conn.execute("DELETE FROM venue_arb.opportunities WHERE source = 'ladder' AND kalshi_id LIKE 'ZZTEST-%'")
        conn.execute("DELETE FROM venue_arb.listings WHERE market_id LIKE 'ZZTEST-%'")


def counts():
    from qa import db

    (edges,) = db.ch().query("SELECT count() FROM venue_arb.ladder_edges FINAL").result_rows[0]
    (late,) = db.ch().query("SELECT count() FROM venue_arb.ladder_edges WHERE ts >= instant").result_rows[0]
    (mine,) = db.ch().query("SELECT count() FROM venue_arb.ladder_edges FINAL WHERE k_id LIKE 'ZZTEST-%'").result_rows[0]
    with db.pg() as conn:
        (opps,) = conn.execute("SELECT count(*) FROM venue_arb.opportunities WHERE source = 'ladder'").fetchone()
    return edges, late, mine, opps


@pytest.mark.integration
def test_ladder_flow_idempotent():
    from venue_arb.flows.ladder_score import ladder_score_flow

    end = datetime.now(UTC).replace(second=0, microsecond=0) - timedelta(minutes=10)
    _cleanup()  # a previous aborted run
    try:
        _seed(end)
        first = ladder_score_flow(window_hours=2, end=end)  # raises if any error check fails
        after_first = counts()
        second = ladder_score_flow(window_hours=2, end=end)
        print(f"first={first} second={second} counts={after_first}")
        assert first == second and first["ladders"] > 0
        assert counts() == after_first and after_first[1] == 0
        # synthetic ladder: near-exact 80,000 (both directions) + arb 80,200 (k_yes_pm_no), 6 snapshots each
        assert after_first[2] == 3 * 6 and first["edge_rows"] >= 18 and first["episodes"] >= 1
    finally:
        _cleanup()
