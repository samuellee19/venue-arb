from datetime import UTC, datetime, timedelta

from venue_arb.exact import ExactPair
from venue_arb.matching.adjudicate import PROMPT_VERSION
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
    assert set(s) == {"generated_at", "note", "pairs", "pair_list", "fees", "screened", "executable", "jurisdiction",
                      "ladder"}
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


def test_render_sections_and_tiers_labeled(tmp_path, monkeypatch):
    monkeypatch.setattr(note, "NEEDS_REVIEW", {"K-1": {0: "x"}})
    s = note.summarize([pair()], [screened(h=h, net=-0.03 + h / 1000) for h in range(50)],
                       [edge(m=m) for m in range(10)], [], adjudications={("K-1", "0xpm"): {"tail_risks": "Cancelled meeting resolves No", "model": "m", "prompt_version": "v2"}})
    text = note.render(s, tmp_path).read_text()
    for h in ("## Question", "## Data and window", "## Method", "## Results", "## Limits", "## Next",
              "Contract-term differences (tail risk)", "k_price < 1", "Cancelled meeting resolves No",
              "venue_arb.screened_v2", "biased in both directions", "end_period_ts", "not to payout",
              "up to about a cent per contract at mid prices", "jurisdictions each venue admits is itself unverified",
              "builder's own reading"):
        assert h in text, h
    assert "conservative" not in text and "fraction of a cent" not in text
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


def test_tail_flags_pair_and_clause(monkeypatch):
    monkeypatch.setattr(note, "NEEDS_REVIEW", {"K": {0: "pair reason", 2: "clause reason"}})
    lines = note._tail({"kalshi_id": "K", "tail_risks": "A first; B second"})
    assert "pair reason" in lines[0] and "clause reason" not in lines[0]
    assert lines[1] == "  - A first" and lines[2].startswith("  - B second *(flagged") and "clause reason" in lines[2]


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
        "wrong/unsure review": 1, f"no prompt-{PROMPT_VERSION} adjudication": 1}}
    assert s["executable"]["rows"] == 0 and s["executable"]["scope"]["excluded"] == {"wrong/unsure review": 1}


def test_render_rule_pairs_no_flag_intro_and_method(tmp_path):
    a = {("K-1", "0xpm"): {"tail_risks": "t", "model": "rule:fed-v1", "prompt_version": "v3"}}
    s = note.summarize([pair()], [screened(h=h) for h in range(50)], [edge(m=m) for m in range(10)], [], adjudications=a)
    text = note.render(s, tmp_path).read_text()
    assert "builder's own reading" not in text
    assert "by a deterministic rule (rule:fed-v1 (prompt v3))" in text and "by an LLM" not in text


INSTANT = T0 + timedelta(hours=10)


def lgroup(kind="arb", d="k_yes_pm_no", k="KXBTCD-T79999.99", pm="pm-80200", a=79999.99, b=80200.0, n=10, pos=0,
           best=-0.02, up=150.0, down=60.0):
    return {"asset": "BTC", "instant": INSTANT, "k_id": k, "pm_id": pm, "direction": d, "kind": kind, "a": a, "b": b,
            "basis_up": up, "basis_down": down, "snapshots": n, "n_capacity_gt0": pos, "best_edge_max": best,
            "first_ts": T0, "last_ts": T0 + timedelta(hours=2)}


def lepisode(minutes=3, cap=150.0):
    return {**episode(cap=cap), "kalshi_id": "KXBTCD-T79999.99", "pm_id": "pm-80200",
            "end_ts": T0 + timedelta(minutes=minutes)}


ADM = [{"hour": T0, "checks": 50}, {"hour": T0, "checks": 50}, {"hour": T0 + timedelta(hours=1), "checks": 30}]
HOURS = [{"hour": T0, "scored": 10, "pos": 2}, {"hour": T0 + timedelta(hours=1), "scored": 5, "pos": 0},
         {"hour": T0 + timedelta(hours=2), "scored": 5, "pos": 0}]
BASIS = [("BTC", -40.0 + i % 7, 80_000.0) for i in range(30)] + [("ETH", 1.0, 2_500.0)] * 5


def test_ladder_tier_separate_and_counted(tmp_path):
    groups = [lgroup(n=10, pos=2, best=0.03), lgroup(d="pm_yes_k_no", pm="pm-79800", b=79800.0, n=10),
              lgroup(kind="near_exact", pm="pm-80000", b=80000.0, n=10, best=0.004),
              lgroup(kind="near_exact", d="pm_yes_k_no", pm="pm-80000", b=80000.0, n=10)]
    s = note.summarize([pair()], [screened()], [edge()], [episode()], ladder_rows=groups,
                       ladder_episodes=[lepisode()], basis_rows=BASIS, admissible=ADM, ladder_hours=HOURS)
    lad = s["ladder"]
    assert lad["ladders"] == 1 and lad["kalshi_strikes"] == 1 and lad["pm_strikes"] == 3
    assert lad["arb_checks_scored"] == 20 and lad["arb_checks_pos"] == 2 and lad["share_scored"] == 0.1
    # hour T0 counted twice (manual re-run) is kept once; hour T0+2h was never counted, so its rows stay out of the share
    assert lad["counted_hours"] == 2 and lad["admissible_checks"] == 80 and lad["pos_counted"] == 2
    assert lad["scored_counted"] == 15 and lad["share_admissible"] == 2 / 80 and lad["best_edge_max"] == 0.03
    assert lad["episodes"] == 1 and lad["duration_min_max"] == 3.0 and lad["capacity_usd_max"] == 150.0
    assert lad["hours"] == 2.0
    assert len(lad["near_exact"]) == 2 and {r["exposure"] for r in lad["near_exact"]} == {60.0, 150.0}
    b = lad["basis"]
    assert b["BTC"]["n"] == 30 and b["BTC"]["down"] == 40.0 and b["BTC"]["up"] == 0.0
    assert b["BTC"]["mean_bps"] < 0 and b["ETH"]["up"] is None  # 5 rows < LADDER_MIN_BASIS: no bound
    # tiers never mixed: ladder inputs leave the pair tiers untouched and vice versa
    base = note.summarize([pair()], [screened()], [edge()], [episode()])
    assert base["executable"] == s["executable"] and base["screened"] == s["screened"]
    assert base["jurisdiction"] == s["jurisdiction"]
    other = note.summarize([pair()], [screened(net=0.5)], [edge(best=0.5, cap=999.0)], [], ladder_rows=groups,
                           ladder_episodes=[lepisode()], basis_rows=BASIS, admissible=ADM, ladder_hours=HOURS)
    assert other["ladder"] == lad
    text = note.render(s, tmp_path).read_text()
    for h in ("Near-exact same-strike pairs (not arbitrage)", "2 of 80 admissible", "disjoint hours",
              "80,000.00", "no ladder snapshots yet"):
        assert (h in text) == (h != "no ladder snapshots yet"), h


def test_ladder_empty_renders_plainly(tmp_path):
    s = note.summarize([pair()], [screened(h=h) for h in range(5)], [edge(m=m) for m in range(3)], [],
                       basis_rows=BASIS)
    lad = s["ladder"]
    assert lad["arb_checks_scored"] == 0 and lad["episodes"] == 0 and lad["admissible_checks"] is None
    assert lad["share_admissible"] is None and lad["hours"] == 0.0 and lad["counted_hours"] == 0
    text = note.render(s, tmp_path).read_text()
    assert "Same-instant crypto ladders (model-free)" in text and "no ladder snapshots yet" in text
    for h in ("ladder_edges", "source = 'ladder'", "0.999", "X - 0.01", "gapped", "BRTI sits below Binance"):
        assert h in text, h
    assert "ladder_admissible_checks" in text and "lower bound" not in text
    assert "BTC n = 30 hours, the 0.999 nearest-rank quantile is the largest" in text


def test_pair_tier_query_excludes_ladders():
    assert "source = 'pair'" in note.OPPS_SQL and "source = 'ladder'" in note.LADDER_OPPS_SQL


def test_ladder_positive_is_capacity_contracts():
    for sql in (note.LADDER_SQL, note.LADDER_HOURS_SQL):
        assert "capacity_contracts > 0" in sql and "capacity_usd > 0" not in sql
