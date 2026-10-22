from datetime import UTC, datetime, timedelta

from tests.test_note import ADM, BASIS, HOURS, T0, edge, episode, pair, screened
from venue_arb.bundle import ResearchSummary
from venue_arb.report import copy, note, research

NOW = datetime(2026, 10, 21, 12, 0, tzinfo=UTC)


def reduce(s, review=None):
    return research.from_summary(s, review=review, computed_at=NOW)


def test_from_summary_headline_numbers():
    s = note.summarize([pair(), pair("K-2")], [screened()], [edge(m=0, best=0.0192, cap=300.0), edge(m=1)],
                       [episode(), episode(), episode(us=True)])
    r = reduce(s)
    assert r.headline[0] == copy.HEADLINE["question"] and len(r.headline) == 3
    assert "3 episodes" in r.headline[1] and "1.9¢" in r.headline[1] and "19 Oct" in r.headline[1]
    assert r.max_net_edge_cents == 1.92 and r.episodes_pair == 3 and r.episodes_us == 1 and r.snapshots == 2
    assert r.pairs_eligible == 2 and r.window_start == T0 and r.method == copy.METHOD
    assert r.note_path == "reports/research-note-v1.md"
    empty = reduce(note.summarize([], [], [], []))
    assert "—" in empty.headline[1] and "0 episodes" in empty.headline[1] and empty.max_net_edge_cents is None
    assert empty.headline[2] == copy.HEADLINE["ladders_none"]
    one = reduce(note.summarize([pair()], [], [edge()], [episode()])).headline[1]
    assert "1 exact pair and 1 order-book snapshot " in one and "1 episode met" in one


def test_exec_hourly_is_hourly_max():
    rows = [edge(m=0, best=0.01), edge(m=30, best=0.03), edge("K-2", m=30, best=0.05, d="k_no_pm_yes"),
            edge(m=70, best=0.002)]
    h = reduce(note.summarize([pair()], [], rows, [])).exec_hourly
    assert h.ts == [T0, T0 + timedelta(hours=1)] and h.values == [5.0, 0.2]
    assert reduce(note.summarize([], [], [], [])).exec_hourly.ts == []


def test_screened_hist_bins():
    vals = [-0.5, -0.105, -0.10, -0.03, 0.0, 0.0099, 0.01, 0.0999, 0.10, 0.5]
    hist = reduce(note.summarize([pair()], [screened(h=i, net=v) for i, v in enumerate(vals)], [], [])).screened_hist
    assert len(hist.edges) == 23 and len(hist.counts) == 22 and hist.edges[0] == -11 and hist.edges[-1] == 11
    assert sum(hist.counts) == len(vals)
    # < -10¢ pools in the first bin, >= +10¢ in the last; bins are [k, k+1)
    assert hist.counts[0] == 2 and hist.counts[1] == 1 and hist.counts[8] == 1 and hist.counts[11] == 2
    assert hist.counts[12] == 1 and hist.counts[20] == 1 and hist.counts[21] == 2


def test_ladder_hourly_counted_hours_only():
    adm = [*ADM, {"hour": T0 + timedelta(hours=3), "checks": 7}]  # counted, but no scored row: 0 violations
    lh = reduce(note.summarize([pair()], [], [], [], admissible=adm, ladder_hours=HOURS)).ladder_hourly
    assert lh.ts == [T0, T0 + timedelta(hours=1), T0 + timedelta(hours=3)]  # T0+2h scored but never counted
    assert lh.checks == [50, 30, 7] and lh.violations == [2, 0, 0]


def test_review_none_passthrough():
    s = note.summarize([pair()], [], [], [])
    assert reduce(s).review is None
    rv = {"precision": 0.97, "labels": 31, "asof": NOW}
    assert reduce(s, review=rv).review == rv


def test_from_summary_validates():
    hours = range(60 * 24)
    s = note.summarize([pair()], [screened(h=h, net=(h % 40 - 20) / 1000) for h in hours],
                       [edge(m=60 * h, best=(h % 30) / 1000) for h in hours], [episode()], basis_rows=BASIS,
                       admissible=[{"hour": T0 + timedelta(hours=h), "checks": 40} for h in hours],
                       ladder_hours=[{"hour": T0 + timedelta(hours=h), "scored": 10, "pos": h % 2} for h in hours])
    r = reduce(s, review={"precision": 1.0, "labels": 31, "asof": NOW})
    assert isinstance(r, ResearchSummary) and len(r.exec_hourly.ts) == 1440 and len(r.ladder_hourly.ts) == 1440
    assert "1,440 order-book snapshots" in r.headline[1] and "of 57,600 admissible" in r.headline[2]
    assert set(r.basis) == {"BTC"} and r.basis["BTC"].down == 40.0  # ETH has too few hours for a bound
    j = r.model_dump_json()
    # compare as JSON: `review` is a plain dict, so its datetime comes back as a string
    assert ResearchSummary.model_validate_json(j).model_dump_json() == j and len(j) < 150_000
