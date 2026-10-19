from datetime import UTC, datetime, timedelta

import pytest

from venue_arb import scoring
from venue_arb.episodes import Episode
from venue_arb.exact import ExactPair

T0 = datetime(2026, 1, 1)  # noqa: DTZ001 - ClickHouse may hand back naive UTC
FEE0 = {"fee_type": "quadratic", "fee_multiplier": 0}
PAIR = ExactPair("K", "P", "tok", "yes", datetime(2026, 2, 1, tzinfo=UTC), "KXS", None, False, 0.9, False)


def s(n):
    return T0 + timedelta(seconds=n)


def test_snapshots_group_by_market_and_time_best_first():
    rows = [(s(0), "kalshi", "K", "yes", 1, 0.41, 5.0), (s(0), "kalshi", "K", "yes", 0, 0.40, 10.0),
            (s(0), "kalshi", "K", "no", 0, 0.62, 7.0), (s(60), "kalshi", "K", "yes", 0, 0.39, 1.0),
            (s(0), "polymarket", "P", "no", 0, 0.55, 3.0)]
    out = scoring.snapshots(rows)
    t = s(0).replace(tzinfo=UTC)
    assert set(out) == {("kalshi", "K"), ("polymarket", "P")}
    assert list(out[("kalshi", "K")]) == [t, s(60).replace(tzinfo=UTC)]
    assert [(lv.side, lv.level, lv.price) for lv in out[("kalshi", "K")][t]] == \
        [("no", 0, 0.62), ("yes", 0, 0.40), ("yes", 1, 0.41)]


def test_pair_points_aligns_and_stamps_kalshi_time():
    rows = [(s(0), "kalshi", "K", "yes", 0, 0.40, 500.0), (s(10), "polymarket", "P", "no", 0, 0.55, 300.0),
            (s(60), "kalshi", "K", "yes", 0, 0.40, 500.0), (s(200), "polymarket", "P", "no", 0, 0.55, 300.0)]
    snaps = scoring.snapshots(rows)
    pts = scoring.pair_points(PAIR, snaps[("kalshi", "K")], snaps[("polymarket", "P")], FEE0)
    assert set(pts) == {"k_yes_pm_no", "k_no_pm_yes"}
    [(t, e)] = pts["k_yes_pm_no"]  # s(60) has no Polymarket snapshot within 30 s
    assert t == s(0).replace(tzinfo=UTC) and e.capacity_contracts == 300 and e.avg_edge == pytest.approx(0.05)
    [(_, other)] = pts["k_no_pm_yes"]
    assert other.capacity_contracts == 0 and other.best_edge is None


def test_edge_row_and_opportunity_row():
    t = s(0).replace(tzinfo=UTC)
    [(_, e)] = scoring.pair_points(PAIR, {t: scoring.snapshots([(s(0), "kalshi", "K", "yes", 0, 0.4, 500.0)])
                                          [("kalshi", "K")][t]},
                                   {t: scoring.snapshots([(s(0), "polymarket", "P", "no", 0, 0.55, 300.0)])
                                    [("polymarket", "P")][t]}, FEE0)["k_yes_pm_no"]
    row = scoring.edge_row(PAIR, t, e, "2026-10-19")
    assert row == {"ts": t, "kalshi_id": "K", "pm_id": "P", "direction": "k_yes_pm_no", "best_edge": e.best_edge,
                   "capacity_contracts": 300.0, "capacity_usd": e.capacity_usd, "avg_edge": e.avg_edge,
                   "fee_version": "2026-10-19"}
    ep = Episode("K", "P", "k_yes_pm_no", t, t + timedelta(minutes=1), 2, 0.05, 285.0, 1.5)
    opp = scoring.opportunity_row(ep)
    assert opp["us_executable"] is False  # Polymarket is geoblocked for US persons
    assert opp["transfer_cost_usd"] == pytest.approx(10.0 + 285.0 * 10 / 1e4)
    assert (opp["start_ts"], opp["end_ts"], opp["snapshots"], opp["max_edge"]) == (ep.start, ep.end, 2, 0.05)
