import json
import re
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from venue_arb import config
from venue_arb.bundle import (
    LIVE_MAX_AGE_S,
    Basis,
    LiveBundle,
    build_live,
    exact_pair_rows,
    ladder_pair_rows,
    market_url,
    verdict,
)
from venue_arb.edges import Edge
from venue_arb.exact import ExactPair
from venue_arb.fees import transfer_cost
from venue_arb.matching.crypto_rule import Strike
from venue_arb.models import Level

FIX = Path(__file__).parent / "fixtures" / "bundle"
EXF = json.loads((FIX / "exact_pair.json").read_text())
LDF = json.loads((FIX / "ladder.json").read_text())
NOW = datetime.fromisoformat(EXF["now"])


def _levels(xs):
    return [Level(**x) for x in xs]


def _titles(fx):
    return {(r["venue"], r["market_id"]): r for r in fx["listings"]}


PAIR = ExactPair(**{**EXF["pair"], "close_time": datetime.fromisoformat(EXF["pair"]["close_time"])})
SERIES_FEE = EXF["series_fee"]
K_BOOK = (datetime.fromisoformat(EXF["k_book"]["ts"]), _levels(EXF["k_book"]["levels"]))
PM_BOOK = (datetime.fromisoformat(EXF["pm_book"]["ts"]), _levels(EXF["pm_book"]["levels"]))
TITLES = _titles(EXF) | _titles(LDF)

INSTANT = datetime.fromisoformat(LDF["instant"])
LADDER = {"kalshi": [Strike("kalshi", s["market_id"], None, "BTC", INSTANT, "gt", s["level"]) for s in LDF["kalshi"]],
          "polymarket": [Strike("polymarket", s["market_id"], s["yes_token"], "BTC", INSTANT, "gt", s["level"])
                         for s in LDF["polymarket"]]}
BOOKS = {(v, mid): (NOW - timedelta(seconds=30), _levels(LDF["books"][mid]))
         for v in ("kalshi", "polymarket") for mid in LDF["books"] if (v == "kalshi") == mid.startswith("KX")}


def K_FEE(market_id):
    return LDF["series_fee"]


def PM_FEE(market_id):
    return LDF["pm_fee"]["schedule"], LDF["pm_fee"]["fees_enabled"]


BASIS = {"BTC": Basis(up=182, down=152, window_days=30, asof=NOW)}
KPI = {"checks_window": [NOW - timedelta(hours=1), NOW], "admissible_checks": 1200, "violations": 0,
       "screened_share_ge_1c": 0.004, "screened_hours": 3100}


@pytest.fixture(autouse=True)
def _books_on(monkeypatch):
    monkeypatch.setattr(config, "DASHBOARD_SHOW_BOOKS", True)


def _ex():
    return exact_pair_rows(PAIR, TITLES, K_BOOK, PM_BOOK, SERIES_FEE, {}, NOW)


def _lad():
    return ladder_pair_rows("BTC", INSTANT, LADDER, BOOKS, 182, 152, K_FEE, PM_FEE, TITLES, {}, NOW)


def _live(ex, lad):
    return build_live(now=NOW, exact_rows=ex, ladder_rows=lad, basis=BASIS, episodes=[], annotations=[],
                      kpi_inputs=KPI, attribution=["Kalshi", "Polymarket"], caveats=[], collector_last=NOW)


def test_exact_pair_rows_both_directions_and_edge():
    rows = exact_pair_rows(PAIR, TITLES, K_BOOK, PM_BOOK, SERIES_FEE, {}, NOW)
    by = {r.direction: r for r in rows}
    assert set(by) == {"k_yes_pm_no", "k_no_pm_yes"} and by["k_yes_pm_no"].kind == "exact"
    r = by["k_yes_pm_no"]
    assert r.net_edge_cents == pytest.approx(100 * (1 - 0.41 - 0.58) - 1.7 - 0.0, abs=0.05)   # Kalshi fee 1.7¢
    assert r.net_edge_bps == pytest.approx(r.net_edge_cents / (100 - r.net_edge_cents) * 1e4, rel=1e-3)
    assert r.books[0][0].price == 0.41 and r.key == "KXFED-26DEC-H0~0xabc~k_yes_pm_no"
    assert r.verdict.label == "Exact match · no edge" and r.live
    # beyond the brief: the derived fields and fixed check order
    assert r.fees_cents == pytest.approx(1.7) and [lv.price for lv in r.books[1]] == [0.58, 0.59]
    assert [(lg.venue, lg.side) for lg in r.legs] == [("kalshi", "yes"), ("polymarket", "no")]
    assert [c.name for c in r.verdict.checks] == ["exact_match", "basis_bound", "fees_depth", "jurisdiction"]
    assert r.verdict.checks[1].status == "ok" and r.verdict.checks[1].detail == "exact match: same settlement source"
    assert r.verdict.checks[2].detail == "net -0.7¢ at the first 120 contracts; fees 1.7¢"
    assert r.verdict.checks[3].detail == "anywhere: both legs open · US: Polymarket rejects US orders"
    assert not r.us_executable and r.spark == [None] * 24
    assert r.fee_params == {"k": {"fee_type": "quadratic_with_maker_fees", "fee_multiplier": 1},
                            "pm": {"schedule": PAIR.pm_fee_schedule, "fees_enabled": False}}
    assert by["k_no_pm_yes"].verdict.label == "No asks on one side"  # the fixture has no Kalshi No / PM Yes asks


def test_pair_not_live_when_leg_stale():
    stale = (NOW - timedelta(seconds=LIVE_MAX_AGE_S + 1), PM_BOOK[1])
    r = exact_pair_rows(PAIR, TITLES, K_BOOK, stale, SERIES_FEE, {}, NOW)[0]
    assert not r.live and r.net_edge_cents is None and r.verdict.label.startswith("Stale")


def test_pair_with_empty_book():
    r = exact_pair_rows(PAIR, TITLES, K_BOOK, (NOW, []), SERIES_FEE, {}, NOW)[0]
    assert r.net_edge_cents is None and r.verdict.label == "No asks on one side"


def test_ladder_rows_collapse_to_best_pm_strike_per_kalshi_strike_and_direction():
    rows = ladder_pair_rows("BTC", INSTANT, LADDER, BOOKS, 182, 152, K_FEE, PM_FEE, TITLES, {}, NOW)
    keys = {(r.legs[0].market_id, r.direction, r.kind) for r in rows}
    assert ("KXBTCD-26OCT2011-T85999.99", "k_yes_pm_no", "near_exact") in keys   # 85999.99 vs 86000
    assert ("KXBTCD-26OCT2011-T85999.99", "k_yes_pm_no", "ladder") in keys       # vs 88200 (gap >= 152)
    assert all(r.ladder == "BTC~2026-10-20T16:00:00+00:00" for r in rows)
    near = next(r for r in rows if r.kind == "near_exact")
    assert near.verdict.label == "Not an arbitrage · basis exposure"
    assert near.verdict.checks[1].detail == "needs PM − K ≥ $152 (99.9th pct, 30 d); gap $0"
    # beyond the brief: 2 near-exact rows + 3 ladder rows (85999.99/88200, 87999.99/88200, 87999.99/86000)
    assert len(rows) == 5 and len(keys) == 5


def test_executable_edge_label_carries_transfer_cost():
    r = next(r for r in _lad() if r.kind == "ladder" and r.legs[0].market_id.endswith("85999.99"))
    # first slice: 500 x (0.93 + 0.04); second: 100 x (0.94 + 0.04); third (0.94 + 0.05) nets < 1¢
    assert r.capacity_contracts == 600 and r.capacity_usd == pytest.approx(583)
    net = 100 * (1 - 0.97) - 228 / 500 - 100 * 0.07 * 0.04 * 0.96
    assert r.net_edge_cents == pytest.approx(net, abs=1e-6)
    after = 583 - transfer_cost(583)
    assert r.verdict.label == f"Executable edge · +{net:.1f}¢ · $583 before transfer · ${after:,.0f} after"
    assert r.verdict.label == "Executable edge · +2.3¢ · $583 before transfer · $572 after"
    assert r.verdict.checks[1].status == "ok" and r.verdict.checks[2].status == "ok"


def test_build_live_kpis_and_schema_roundtrip():
    b = _live(_ex(), _lad())
    # the brief expected ladder 2; the brief's own strikes give 3 admissible (strike, direction) rows, all live
    assert b.kpis.pairs_live == {"exact": 2, "near_exact": 2, "ladder": 3}
    # controller ruling: near-exact rows (basis exposure) never head the best-edge tile
    assert b.kpis.best_edge["key"] == max((p for p in b.pairs if p.kind != "near_exact"),
                                          key=lambda p: p.net_edge_cents or -9).key
    assert b.kpis.best_edge["key"] == "KXBTCD-26OCT2011-T85999.99~0xb88200~k_yes_pm_no"
    lad = [r.model_copy(update={"net_edge_cents": 99.0}) if r.kind == "near_exact" else r for r in _lad()]
    assert _live(_ex(), lad).kpis.best_edge["key"] == b.kpis.best_edge["key"]  # a 99¢ near-exact row is ignored
    assert LiveBundle.model_validate_json(b.model_dump_json()) == b
    assert len(b.model_dump_json()) < 200_000


def test_books_hidden_when_flag_off(monkeypatch):
    monkeypatch.setattr(config, "DASHBOARD_SHOW_BOOKS", False)
    b = _live(_ex(), _lad())
    assert not b.show_books and all(p.books is None for p in b.pairs)
    js = b.model_dump_json()
    assert '"price"' not in js and '"size"' not in js
    details = [c.detail for p in b.pairs for c in p.verdict.checks]
    sizes = {f"{x['size']:,.0f}" for fx in (EXF["k_book"], EXF["pm_book"]) for x in fx["levels"]}
    sizes |= {f"{x['size']:,.0f}" for xs in LDF["books"].values() for x in xs}
    numbers = {n for d in details for n in re.findall(r"(?<![\d,.$])\d[\d,]*(?:\.\d+)?", d)}  # $ amounts excluded
    assert numbers and not numbers & sizes
    r = b.pairs[0]  # exact k_yes_pm_no
    assert r.verdict.checks[2].detail == "net -0.7¢ on the first slice; fees 1.7¢"


def test_verdict_without_first_slice():
    e = Edge("k_yes_pm_no", 0.02, 500, 480, 0.02)
    v = verdict("exact", "k_yes_pm_no", e, a=None, b=None, up=None, down=None, us_executable=False, both_live=True)
    assert v.checks[2].detail == "net +2.0¢ on the first slice" and v.label.startswith("Executable edge · +2.0¢")


def test_verdict_label_rounds_half_up_like_the_client():
    # 100 * 0.0535 is published as 5.35; toLocaleString shows +5.4¢ and $151 (Python's .1f/.0f say 5.3 and 150)
    v = verdict("exact", "k_yes_pm_no", Edge("k_yes_pm_no", 0.0535, 1000, 150.5, 0.05), a=None, b=None, up=None,
                down=None, us_executable=True, both_live=True)
    assert v.label == "Executable edge · +5.4¢ · $151 before transfer · $140 after"
    assert v.checks[2].detail == "net +5.4¢ on the first slice"


def test_market_urls():
    assert market_url("kalshi", {"market_id": "KXBTCD-26OCT2011-T85999.99", "raw": {"event": {"series_ticker": "KXBTCD", "event_ticker": "KXBTCD-26OCT2011"}}}) == "https://kalshi.com/markets/kxbtcd/market/kxbtcd-26oct2011"
    assert market_url("polymarket", {"raw": {"event": {"slug": "bitcoin-above-86000-on-october-20-12pm-et"}}}) == "https://polymarket.com/event/bitcoin-above-86000-on-october-20-12pm-et"
