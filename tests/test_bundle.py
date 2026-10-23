import json
import re
from dataclasses import replace
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from venue_arb import config
from venue_arb.bundle import (
    LIVE_MAX_AGE_S,
    Basis,
    LiveBundle,
    Pair,
    ResearchHist,
    ResearchLadderSeries,
    ResearchSeries,
    ResearchSummary,
    build_live,
    exact_pair_rows,
    ladder_pair_rows,
    market_url,
    roi,
    stock_perp_rows,
    verdict,
)
from venue_arb.edges import Edge
from venue_arb.exact import ExactPair
from venue_arb.fees import transfer_cost
from venue_arb.matching.crypto_rule import Strike
from venue_arb.models import Level
from venue_arb.sources.hyperliquid import Label, PerpCtx, PerpLevel
from venue_arb.sources.schwab import Quote

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
    # distinct market pairs: the exact pair's two directions count once, as do the near-exact pair's
    assert b.kpis.pairs_live == {"exact": 1, "near_exact": 1, "ladder": 3}
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


def test_net_edge_roi():
    r = next(r for r in _lad() if r.kind == "ladder" and r.net_edge_cents is not None)
    assert r.net_edge_roi == pytest.approx(r.net_edge_cents / (100 - r.net_edge_cents))
    stale = exact_pair_rows(PAIR, TITLES, K_BOOK, (NOW - timedelta(seconds=LIVE_MAX_AGE_S + 1), PM_BOOK[1]),
                            SERIES_FEE, {}, NOW)[0]
    assert stale.net_edge_cents is None and stale.net_edge_roi is None
    assert roi(0.5) == pytest.approx(0.005 / 0.995) and roi(None) is None  # 0.5¢ net on a 99.5¢ outlay


def test_pairs_live_counts_pairs_not_directions():
    assert _live(_ex(), []).kpis.pairs_live == {"exact": 1, "near_exact": 0, "ladder": 0}


def test_categories_from_taxonomy_display():
    b = _live(_ex(), _lad())
    assert b.categories == {"fed_macro": "Fed & macro", "crypto_price": "Crypto", "weather": "Weather",
                            "stock_perps": "Stock perps"}
    odd = [r.model_copy(update={"category": "sports"}) for r in _ex()]
    assert _live(odd, []).categories["sports"] == "sports"  # an unknown slug still renders


def test_research_optional():
    b = _live(_ex(), _lad())
    assert b.research is None and LiveBundle.model_validate_json(b.model_dump_json()) == b
    bare = b.model_dump(exclude={"categories", "research"})  # Ruling 5: both default when absent
    assert LiveBundle.model_validate(bare).categories == {} and LiveBundle.model_validate(bare).research is None
    rs = ResearchSummary(
        computed_at=NOW, window_start=None, screened_start=None, screened_end=None, ladder_start=None, headline=["a.", "b."], pairs_eligible=3, pairs_reviewed=1, snapshots=10,
        max_net_edge_cents=None, episodes_pair=0, episodes_ladder=1, episodes_us=0, episode_median_minutes=None,
        episode_median_capacity_usd=None, screened_rows=0, screened_share_ge_1c=None, screened_share_early=None,
        ladder_checks=None, ladder_violations=0, ladder_scored=0, basis=BASIS, basis_q=0.999, review=None,
        exec_hourly=ResearchSeries(ts=[NOW], values=[None]),
        screened_hist=ResearchHist(edges=[float(x) for x in range(-10, 12)], counts=[0] * 21),
        ladder_hourly=ResearchLadderSeries(ts=[], checks=[], violations=[]),
        method={"matching": "m", "edge": "e", "crypto": "c", "not_shown": "n"},
        note_path="reports/research-note-v1.md")
    b2 = build_live(now=NOW, exact_rows=_ex(), ladder_rows=[], basis=BASIS, episodes=[], annotations=[],
                    kpi_inputs=KPI, attribution=[], caveats=[], collector_last=NOW, research=rs)
    assert LiveBundle.model_validate_json(b2.model_dump_json()) == b2 and b2.research == rs


SPF = json.loads((FIX / "stock_perp.json").read_text())
SP_NOW = datetime.fromisoformat(SPF["now"])
CTX215 = PerpCtx(**SPF["ctx"])
LABEL_NVDA = Label(**SPF["label"])
QUOTE = Quote(**{**SPF["quote"], "quote_time": datetime.fromisoformat(SPF["quote"]["quote_time"])})
SP_BOOK = [PerpLevel(**x) for x in SPF["book"]]
SP_SNAP = (datetime.fromisoformat(SPF["book_ts"]), SP_BOOK)


def test_stock_perp_row_executable():  # NVDA mark 215 vs Schwab 212.84, growth fee, 300 shares displayed
    [p] = stock_perp_rows([(CTX215, LABEL_NVDA, SP_SNAP, QUOTE)], {}, SP_NOW)
    assert p.kind == "stock_perp" and p.category == "stock_perps" and p.settles_at is None and p.books is None
    assert p.key == "xyz:NVDA~NVDA~stock_perp" and p.title == "NVIDIA (NASDAQ)" and p.live
    assert p.direction == "short_perp_long_stock"
    assert p.trade == "short xyz:NVDA / long NVDA" and p.reference["kind"] == "schwab" and p.hold_hours == 24
    assert p.reference == {"kind": "schwab", "symbol": "NVDA", "asof": QUOTE.quote_time.isoformat()}
    assert re.fullmatch(r"Executable edge · \+\d+ bps · \$[\d,]+ at top of book", p.verdict.label)
    assert [c.name for c in p.verdict.checks] == ["reference", "fees_carry", "fees_depth", "jurisdiction"]
    assert p.verdict.checks[0].detail == "Schwab quote 14 s old"
    assert p.net_edge_bps == pytest.approx(p.net_bps) and p.net_edge_cents == pytest.approx(p.net_bps / 100)
    assert p.net_edge_roi == pytest.approx(p.net_bps / 1e4) and p.capacity_usd == pytest.approx(300 * 212.88, rel=1e-3)  # the stock leg's 300 shares cap the walk
    assert p.us_executable is False and p.legs[0].url == "https://app.hyperliquid.xyz/trade/xyz:NVDA"
    assert p.legs[1].url is None and (p.legs[0].side, p.legs[1].side) == ("short", "long")
    assert (p.legs[0].venue, p.legs[1].venue, p.legs[1].market_id) == ("hyperliquid", "nasdaq", "NVDA")
    assert p.fee_params == {"perp": {"taker": 0.00009, "growth_mode": True}, "stock": {"borrow_bps_yr": 50.0}}
    assert p.next_funding_at == datetime.fromisoformat("2026-10-22T16:00:00+00:00")
    assert p.funding_bps_h == pytest.approx(0.1) and p.spark == [None] * 24
    s = p.sensitivity
    assert set(s) == {"net_bps_1h", "net_bps_7d", "net_bps_borrow_x10"}
    assert s["net_bps_borrow_x10"] == pytest.approx(p.net_bps)  # short perp / long stock pays no borrow
    assert p.fees_cents == pytest.approx((1.8 + 3.7594) / 100, abs=1e-3)  # 2 x 0.9 bps taker + 0.08/212.84 spread
    assert Pair.model_validate_json(p.model_dump_json()) == p


def test_stock_perp_row_costs_are_the_edges_lines():
    [p] = stock_perp_rows([(CTX215, LABEL_NVDA, SP_SNAP, QUOTE)], {}, SP_NOW)
    c = p.costs
    assert set(c) == {"perp_fee_bps", "perp_spread_bps", "stock_spread_bps", "funding_bps", "borrow_bps"}
    assert c["perp_fee_bps"] == pytest.approx(1.8) and c["stock_spread_bps"] == pytest.approx(3.7594, abs=1e-3)
    assert c["funding_bps"] == pytest.approx(-2.4) and c["borrow_bps"] == 0  # short perp receives funding, pays no borrow
    assert c["perp_spread_bps"] > 0 and p.fees_cents == pytest.approx((c["perp_fee_bps"] + c["stock_spread_bps"]) / 100, abs=1e-3)
    long_p = stock_perp_rows([(replace(CTX215, mark=210.0), LABEL_NVDA, SP_SNAP, QUOTE)], {}, SP_NOW)[0]
    assert long_p.costs["borrow_bps"] == pytest.approx(50.0 * 24 / (24 * 365), abs=1e-3) and long_p.costs["funding_bps"] == pytest.approx(2.4)
    [oracle] = stock_perp_rows([(CTX215, replace(LABEL_NVDA, ref_kind="none"), SP_SNAP, QUOTE)], {}, SP_NOW)
    assert oracle.costs["stock_spread_bps"] is None and oracle.costs["perp_fee_bps"] == pytest.approx(1.8)
    [stale] = stock_perp_rows([(CTX215, LABEL_NVDA, (SP_NOW - timedelta(seconds=301), SP_BOOK), QUOTE)], {}, SP_NOW)
    assert stale.costs is None
    assert Pair.model_validate_json(p.model_dump_json()) == p


def test_stock_perp_row_borrow_x10_on_long_perp():  # mark below the stock: long perp / short stock pays borrow
    ctx = replace(CTX215, mark=210.0)
    [p] = stock_perp_rows([(ctx, LABEL_NVDA, SP_SNAP, QUOTE)], {}, SP_NOW)
    assert p.trade == "long xyz:NVDA / short NVDA"
    borrow = 50.0 * 24 / (24 * 365)
    assert p.sensitivity["net_bps_borrow_x10"] == pytest.approx(p.net_bps - 9 * borrow)


def test_stock_perp_row_oracle_only_never_executable():
    for label, quote in ((replace(LABEL_NVDA, ref_kind="none"), QUOTE),
                         (LABEL_NVDA, replace(QUOTE, quote_time=SP_SNAP[0] - timedelta(seconds=121)))):
        [p] = stock_perp_rows([(CTX215, label, SP_SNAP, quote)], {}, SP_NOW)
        assert p.verdict.label == "Reference closed · oracle only" and p.capacity_usd == 0 and p.reference["kind"] == "oracle"
        assert p.verdict.checks[0].status == "partial"
        assert p.verdict.checks[0].detail == "oracle only: stock market closed or no quote"


def test_stock_perp_row_within_costs():
    book = [replace(lv, price=round(lv.price - 2.1, 2)) for lv in SP_BOOK]  # the book follows the mark
    [p] = stock_perp_rows([(replace(CTX215, mark=212.9), LABEL_NVDA, (SP_SNAP[0], book), QUOTE)], {}, SP_NOW)
    assert p.verdict.label == "Basis · within costs · no edge" and p.live
    assert [c.status for c in p.verdict.checks][1:3] == ["fail", "fail"]


def test_stock_perp_row_stale():
    old = (SP_NOW - timedelta(seconds=301), SP_BOOK)
    [p] = stock_perp_rows([(CTX215, LABEL_NVDA, old, QUOTE)], {}, SP_NOW)
    assert p.verdict.label == "Stale · a leg has no snapshot in 5 min" and not p.live
    assert p.net_bps is None and p.net_edge_cents is None and p.net_edge_bps is None and p.sensitivity is None


def test_stock_perp_row_label_none_and_spark():
    lab = Label("xyz:FOO", "none", None, "", "USD", "xyz:FOO")
    [p] = stock_perp_rows([(replace(CTX215, symbol="xyz:FOO"), lab, SP_SNAP, None)], {"xyz:FOO~xyz:FOO~stock_perp": [1.0] * 24}, SP_NOW)
    assert p.key == "xyz:FOO~xyz:FOO~stock_perp" and p.title == "xyz:FOO" and p.spark == [1.0] * 24
    assert p.trade == "short xyz:FOO / long xyz:FOO"


def test_build_live_stock_perps_kpi_and_best_edge_ignores_them():
    [sp] = stock_perp_rows([(CTX215, LABEL_NVDA, SP_SNAP, QUOTE)], {}, SP_NOW)
    b = build_live(now=SP_NOW, exact_rows=[], ladder_rows=[], basis=BASIS, episodes=[], annotations=[],
                   kpi_inputs={"stock_perps": {"episodes_7d": 3}}, attribution=[], caveats=[], collector_last=None,
                   stock_rows=[sp])
    assert b.kpis.stock_perps == {"names_live": 1, "episodes_7d": 3} and b.kpis.best_edge is None
    assert b.kpis.pairs_live == {"exact": 0, "near_exact": 0, "ladder": 0} and len(b.pairs) == 1
    assert LiveBundle.model_validate_json(b.model_dump_json()) == b
    off = build_live(now=SP_NOW, exact_rows=[], ladder_rows=[], basis=BASIS, episodes=[], annotations=[],
                     kpi_inputs={}, attribution=[], caveats=[], collector_last=None)
    assert off.kpis.stock_perps is None


def test_old_live_json_without_new_fields_still_validates():  # live_v2.json: the sample as of 4d54aa3, before stock perps
    b = LiveBundle.model_validate_json((FIX / "live_v2.json").read_text(encoding="utf-8"))
    assert b.kpis.stock_perps is None and b.pairs[0].basis_bps is None and b.pairs[0].reference is None
    assert b.pairs[0].sensitivity is None and isinstance(b.pairs[0].settles_at, datetime)


def test_stock_perp_row_live_schwab_without_a_book():
    [p] = stock_perp_rows([(CTX215, LABEL_NVDA, (SP_SNAP[0], []), QUOTE)], {}, SP_NOW)
    assert p.live and p.verdict.label == "No asks on one side" and p.capacity_usd == 0
    assert p.verdict.checks[2].name == "fees_depth" and p.verdict.checks[2].status == "fail"
    assert p.verdict.checks[2].detail == "not walked: no perp book"


def test_stock_perp_reference_is_aged_from_the_snapshot_tick():
    # the collector inserts every few ticks: a tick 250 s behind the publisher, its quote 30 s older than the tick
    tick = SP_NOW - timedelta(seconds=250)
    quote = replace(QUOTE, quote_time=tick - timedelta(seconds=30))
    [p] = stock_perp_rows([(CTX215, LABEL_NVDA, (tick, SP_BOOK), quote)], {}, SP_NOW)
    assert p.live and p.reference["kind"] == "schwab" and p.verdict.checks[0].detail == "Schwab quote 30 s old"
    [old] = stock_perp_rows([(CTX215, LABEL_NVDA, (SP_NOW - timedelta(seconds=400), SP_BOOK), quote)], {}, SP_NOW)
    assert old.verdict.label == "Stale · a leg has no snapshot in 5 min" and not old.live


def test_stock_perp_key_ignores_direction():  # a basis sign flip keeps the row's identity, sparks and history
    long_p = stock_perp_rows([(replace(CTX215, mark=210.0), LABEL_NVDA, SP_SNAP, QUOTE)], {}, SP_NOW)[0]
    [short_p] = stock_perp_rows([(CTX215, LABEL_NVDA, SP_SNAP, QUOTE)], {"xyz:NVDA~NVDA~stock_perp": [2.0] * 24}, SP_NOW)
    assert long_p.direction != short_p.direction and long_p.key == short_p.key == "xyz:NVDA~NVDA~stock_perp"
    assert short_p.spark == [2.0] * 24
