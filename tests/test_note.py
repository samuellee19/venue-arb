from datetime import UTC, datetime, timedelta

from venue_arb.exact import ExactPair
from venue_arb.report import note

T0 = datetime(2026, 10, 19, 18, 0, tzinfo=UTC)


def pair(k="K-1", reviewed=False):
    return ExactPair(k, "0xpm", "tok", "yes", T0 + timedelta(days=30), "KXFED", None, False, 0.9, reviewed)


def screened(k="K-1", h=0, net=-0.03):
    return {"ts": T0 + timedelta(hours=h), "kalshi_id": k, "pm_id": "0xpm", "direction": "k_yes_pm_no",
            "net_gap_top": net}


def edge(k="K-1", m=0, best=0.002, cap=0.0, d="k_yes_pm_no"):
    return {"ts": T0 + timedelta(minutes=m), "kalshi_id": k, "pm_id": "0xpm", "direction": d, "best_edge": best,
            "capacity_usd": cap}


def episode(us=False, cap=500.0, edge=0.02, transfer=10.5):
    return {"kalshi_id": "K-1", "pm_id": "0xpm", "direction": "k_yes_pm_no", "start_ts": T0,
            "end_ts": T0 + timedelta(minutes=4), "snapshots": 5, "max_edge": edge, "median_capacity_usd": cap,
            "annualized": 0.3, "us_executable": us, "transfer_cost_usd": transfer}


def test_summarize_tiers_never_mixed():
    s = note.summarize([pair(), pair("K-2", reviewed=True)],
                       [screened(h=0, net=0.02), screened(h=1, net=-0.01), screened(h=2, net=0.005)],
                       [edge(m=0, cap=200.0, best=0.02), edge(m=1), edge(m=1, d="k_no_pm_yes")], [episode()])
    assert {"pairs", "screened", "executable", "jurisdiction"} <= set(s)
    sc, ex = s["screened"], s["executable"]
    assert sc["rows"] == 3 and sc["share_gt0"] == 2 / 3 and sc["share_ge_001"] == 1 / 3 and sc["max"] == 0.02
    assert ex["snapshots"] == 2 and ex["share_capacity_gt0"] == 0.5 and ex["episodes"] == 1
    assert ex["duration_min_max"] == 4.0 and ex["capacity_usd_max"] == 500.0
    # 0.02 * 500 / 0.98 = 10.2 < 10.5 transfer: the episode does not survive the transfer cost
    assert ex["episodes_net_of_transfer"] == 0
    # each tier is computed from its own rows only: changing one tier's input leaves the other untouched
    s2 = note.summarize([pair(), pair("K-2", reviewed=True)], [screened(net=0.3)],
                        [edge(m=0, cap=200.0, best=0.02), edge(m=1), edge(m=1, d="k_no_pm_yes")], [episode()])
    s3 = note.summarize([pair(), pair("K-2", reviewed=True)],
                        [screened(h=0, net=0.02), screened(h=1, net=-0.01), screened(h=2, net=0.005)], [], [])
    assert s2["executable"] == ex and s3["screened"] == sc
    assert set(s) == {"generated_at", "note", "pairs", "pair_list", "fees", "screened", "executable", "jurisdiction"}
    assert s["pairs"] == {"eligible": 2, "reviewed": 1, "unreviewed": 1}


def test_summarize_jurisdiction_zero_with_reason():
    j = note.summarize([pair()], [], [], [episode(us=False), episode(us=False)])["jurisdiction"]
    assert j["us_episodes"] == 0 and j["all_episodes"] == 2
    assert "Polymarket International" in j["reason"]


def test_summarize_empty_inputs():
    s = note.summarize([], [], [], [])
    assert s["pairs"] == {"eligible": 0, "reviewed": 0, "unreviewed": 0}
    assert s["screened"]["rows"] == 0 and s["executable"]["episodes"] == 0
    assert s["jurisdiction"]["us_episodes"] == 0
    assert "no eligible pairs" in s["note"]
    # render must not crash on an empty summary either
    import tempfile
    from pathlib import Path
    with tempfile.TemporaryDirectory() as d:
        out = note.render(s, Path(d))
        assert out.name == "research-note-v1.md" and "no eligible pairs" in out.read_text()


def test_render_sections_and_tiers_labeled(tmp_path):
    s = note.summarize([pair()], [screened(h=h, net=-0.03 + h / 1000) for h in range(50)],
                       [edge(m=m) for m in range(10)], [], adjudications={("K-1", "0xpm"): {"tail_risks": "Cancelled meeting resolves No", "model": "m", "prompt_version": "v2"}})
    text = note.render(s, tmp_path).read_text()
    for h in ("## Question", "## Data and window", "## Method", "## Results", "## Limits", "## Next",
              "Contract-term differences (tail risk)", "k_price < 1", "Cancelled meeting resolves No"):
        assert h in text, h
    assert (tmp_path / "screened-net-gap.png").exists() and (tmp_path / "executable-edge.png").exists()


def test_screened_early_life_concentration():
    sc = note.summarize([pair()], [screened(h=0, net=0.02), screened(h=24 * 8, net=0.02),
                                   screened(h=24 * 8 + 1, net=-0.03)], [], [])["screened"]
    assert sc["n_ge_001"] == 2 and sc["n_ge_001_early"] == 1 and sc["n_early"] == 1


def test_tail_parser_keeps_lowercase_semicolons():
    lines = note._tail({"kalshi_id": "K", "tail_risks": "If cancelled, Kalshi pays; then Polymarket follows; "
                                                       "A 12.5bp move rounds up"})
    assert lines[1:] == ["  - If cancelled, Kalshi pays; then Polymarket follows", "  - A 12.5bp move rounds up"]
    assert note._tail({"kalshi_id": "K", "tail_risks": None}) == ["- **K**: none recorded"]


def test_episode_survives_transfer():
    # 0.05 * 500 / 0.95 = 26.3 > 10.5
    assert note.summarize([pair()], [], [], [episode(edge=0.05)])["executable"]["episodes_net_of_transfer"] == 1


def test_scope_filters_tiers_and_counts():
    scope = {("K-1", "0xpm"): {"reason": None, "closed": False},
             ("K-2", "0xpm"): {"reason": None, "closed": True},
             ("K-3", "0xpm"): {"reason": "wrong/unsure review", "closed": False}}
    s = note.summarize([pair()], [screened(k) for k in ("K-1", "K-2", "K-3", "K-4")],
                       [edge("K-3")], [], scope=scope)
    assert s["screened"]["rows"] == 2
    assert s["screened"]["scope"] == {"pairs": 2, "eligible": 1, "closed": 1, "other": 0, "excluded": {
        "wrong/unsure review": 1, "no prompt-v2 adjudication": 1}}
    assert s["executable"]["rows"] == 0 and s["executable"]["scope"]["excluded"] == {"wrong/unsure review": 1}
