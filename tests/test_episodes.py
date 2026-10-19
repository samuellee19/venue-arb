from datetime import datetime, timedelta, timezone

from venue_arb import episodes
from venue_arb.edges import Edge

T0 = datetime(2026, 1, 1, tzinfo=timezone.utc)
CLOSE = T0 + timedelta(days=10)


def s(n):
    return T0 + timedelta(seconds=n)


def pt(n, avg=0.02, usd=98.0 * 2, contracts=200.0, best=0.03):
    return s(n), Edge("k_yes_pm_no", best, contracts, usd, avg)


def run(points, **kw):
    return episodes.find(points, CLOSE, kalshi_id="K", pm_id="P", direction="k_yes_pm_no", **kw)


def test_align_nearest_within_tolerance():
    out = episodes.align([s(60), s(0)], [s(25), s(70)])
    assert out == [(s(0), s(25)), (s(60), s(70))]


def test_align_drops_far_pairs():
    assert episodes.align([s(0)], [s(40)]) == []


def test_align_uses_each_pm_time_once():
    assert episodes.align([s(0), s(10)], [s(5)]) == [(s(0), s(5))]


def test_align_tie_picks_earlier():
    assert episodes.align([s(10)], [s(20), s(0)]) == [(s(10), s(0))]


def test_episode_needs_two_snapshots():
    assert run([pt(0)]) == []


def test_episode_breaks_on_gap():
    eps = run([pt(0), pt(60), pt(300)])
    assert len(eps) == 1 and eps[0].snapshots == 2
    assert (eps[0].start, eps[0].end) == (s(0), s(60))
    assert (eps[0].kalshi_id, eps[0].pm_id, eps[0].direction) == ("K", "P", "k_yes_pm_no")


def test_episode_capacity_floor():
    assert run([pt(0, usd=99.0), pt(60, usd=99.0)]) == []


def test_unqualified_point_breaks_run():
    assert run([pt(0), pt(60, avg=None), pt(120)]) == []


def test_annualized():
    # 2 contracts-worth: usd 196 / 200 contracts = 0.98 cost; run ends at 60 s so ~10 days left
    (ep,) = run([pt(0), pt(60)])
    assert ep.max_edge == 0.03 and ep.median_capacity_usd == 196.0
    days = (CLOSE - s(60)) / timedelta(days=1)
    assert abs(ep.annualized - 0.02 / 0.98 * 365 / days) < 1e-9
    assert abs(ep.annualized - 0.745) < 0.001
