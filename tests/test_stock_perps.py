from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest

from venue_arb import episodes
from venue_arb import stock_perps as sp
from venue_arb.sources.hyperliquid import Label, PerpCtx, PerpLevel
from venue_arb.sources.schwab import Quote

NOW = datetime(2026, 10, 22, 15, 0, tzinfo=UTC)
CTX = PerpCtx("xyz:NVDA", 212.83, 212.81, 212.83, 6.2e-6, 0.0, 1000.0, 5e6, True, 10)
LABEL_SCHWAB = Label("xyz:NVDA", "schwab", "NVDA", "NASDAQ", "USD", "NVIDIA")
LABEL_NONE = replace(LABEL_SCHWAB, ref_kind="none", ref_symbol=None)
BIDS = [PerpLevel("bid", 0, 214.9, 100.0), PerpLevel("bid", 1, 214.8, 150.0)]
ASKS = [PerpLevel("ask", 0, 215.1, 100.0), PerpLevel("ask", 1, 215.2, 150.0)]
BOOK = BIDS + ASKS
REF_SCHWAB = sp.Reference("schwab", "NVDA", 212.84, 212.83, 212.85, 200.0, 300.0, NOW)
REF_ORACLE = sp.Reference("oracle", "xyz:NVDA", 212.81, None, None, None, None, NOW)
E_OK = sp.score(replace(CTX, mark=215.0), BOOK, REF_SCHWAB)


def test_basis_and_direction():
    assert sp.basis_bps(212.83, 212.81) == pytest.approx(0.94, abs=0.01)
    assert sp.direction(5.0) == "short_perp_long_stock" and sp.direction(-5.0) == "long_perp_short_stock"


def test_reference_older_than_max_age_is_absent():
    q = Quote("NVDA", 212.83, 212.85, 200, 300, None, NOW - timedelta(seconds=121))
    assert sp.reference(CTX, q, LABEL_SCHWAB, NOW).kind == "oracle"
    assert sp.reference(CTX, replace(q, quote_time=NOW - timedelta(seconds=119)), LABEL_SCHWAB, NOW).price == 212.84
    assert sp.reference(CTX, q, LABEL_NONE, NOW).kind == "oracle"


def test_costs_round_trip_and_hold():
    c = sp.costs_bps(taker=0.00009, spread_bps=1.0, funding_h=6.2e-6, borrow_bps_yr=50.0, hold_h=24, side="long_perp_short_stock")
    assert c["perp_fee"] == pytest.approx(1.8) and c["stock_spread"] == 1.0
    assert c["funding"] == pytest.approx(1.488) and c["borrow"] == pytest.approx(50 * 24 / (24 * 365))
    assert sp.costs_bps(taker=0.0009, spread_bps=None, funding_h=0, borrow_bps_yr=50, hold_h=24, side="short_perp_long_stock")["perp_fee"] == pytest.approx(18.0)


def test_funding_sign_by_side():
    kw = {"taker": 0, "spread_bps": 0, "funding_h": 1e-4, "borrow_bps_yr": 0, "hold_h": 1}
    assert sp.costs_bps(side="long_perp_short_stock", **kw)["funding"] == pytest.approx(1.0)
    assert sp.costs_bps(side="short_perp_long_stock", **kw)["funding"] == pytest.approx(-1.0)


def test_borrow_only_when_stock_is_short():
    kw = {"taker": 0, "spread_bps": 0, "funding_h": 0, "borrow_bps_yr": 50, "hold_h": 24}
    assert sp.costs_bps(side="short_perp_long_stock", **kw)["borrow"] == 0.0


def test_walk_perp_partial_level_and_whole_book():
    assert sp.walk_perp(BIDS, "bid", None) == pytest.approx((250.0, 214.9 * 100 + 214.8 * 150, (214.9 * 100 + 214.8 * 150) / 250))
    c, usd, _ = sp.walk_perp(BIDS, "bid", 214.9 * 100 + 214.8 * 10)
    assert c == pytest.approx(110.0) and usd == pytest.approx(214.9 * 100 + 214.8 * 10)
    assert sp.walk_perp(BIDS, "bid", 0.0)[:2] == (0.0, 0.0)


def test_score_walks_perp_bids_and_caps_on_stock_size():
    # mark 215 vs Schwab mid 212.84 -> +101.5 bps; short perp: walk bids; stock asks 300 shares at 212.85 = $63,855
    e = sp.score(replace(CTX, mark=215.0), BOOK, REF_SCHWAB)
    assert e.direction == "short_perp_long_stock" and e.basis_bps == pytest.approx(101.5, abs=0.1)
    assert e.capacity_usd == pytest.approx(min(63_855.0, sum(b.price * b.size for b in BIDS)), rel=1e-6)
    fill_bps = (e.capacity_usd / e.capacity_contracts / e.reference - 1) * 1e4
    assert e.net_bps == pytest.approx(fill_bps - e.perp_fee_bps - e.perp_spread_bps - e.stock_spread_bps - e.funding_bps - e.borrow_bps)
    # hand-computed constant: the whole bid side fills (53,710 < 63,855), avg (214.9*100 + 214.8*150)/250 = 214.84
    # fill 2/212.84 = 93.967 bps; fee 2e4*0.00009 = 1.8; perp exit half-spread 0.2/(2*215) = 4.651; stock spread
    # 0.02/212.84 = 0.940; funding (short receives) -6.2e-6*24*1e4 = -1.488; borrow 0
    assert e.net_bps == pytest.approx(93.9673 - 1.8 - 4.6512 - 0.9397 + 1.488, abs=1e-3) and e.net_bps == pytest.approx(88.0645, abs=1e-3)
    assert e.perp_spread_bps == pytest.approx(4.6512, abs=1e-3)


def test_score_stock_cap_binds_and_long_perp_side():
    e = sp.score(replace(CTX, mark=215.0), BOOK, replace(REF_SCHWAB, ask_size=100.0))
    assert e.stock_cap_usd == pytest.approx(21_285.0) and e.capacity_usd == pytest.approx(21_285.0)
    e = sp.score(replace(CTX, mark=210.0), BOOK, REF_SCHWAB)  # mark below stock: long perp (walk asks), short stock
    assert e.direction == "long_perp_short_stock" and e.stock_cap_usd == pytest.approx(200 * 212.83)
    assert e.capacity_usd == pytest.approx(200 * 212.83)
    fill_bps = -(e.capacity_usd / e.capacity_contracts / e.reference - 1) * 1e4  # walked asks, long perp
    assert e.net_bps == pytest.approx(fill_bps - e.perp_fee_bps - e.perp_spread_bps - e.stock_spread_bps - e.funding_bps - e.borrow_bps)
    assert e.borrow_bps > 0 and e.funding_bps > 0


def test_zero_stock_size_gives_zero_capacity():
    e = sp.score(replace(CTX, mark=215.0), BOOK, replace(REF_SCHWAB, ask_size=0.0))
    assert e.capacity_usd == 0.0 and e.capacity_contracts == 0.0 and e.net_bps > 10


def test_oracle_reference_has_no_capacity_or_spread():
    e = sp.score(replace(CTX, mark=215.0), BOOK, REF_ORACLE)
    assert e.stock_spread_bps is None and e.capacity_usd == 0.0 and e.ref_kind == "oracle"
    # nothing walked: entry at the best bid 214.9 vs oracle 212.81, then fee, exit half-spread, funding
    assert e.net_bps == pytest.approx((214.9 / 212.81 - 1) * 1e4 - 1.8 - 4.6512 + 1.488, abs=1e-3)


def test_one_sided_or_crossed_quote_falls_back_to_oracle():
    fresh = NOW - timedelta(seconds=5)
    for bid, ask in [(0.0, 212.85), (212.90, 212.85), (-1.0, 212.85)]:
        q = Quote("NVDA", bid, ask, 200, 300, None, fresh)
        assert sp.reference(CTX, q, LABEL_SCHWAB, NOW).kind == "oracle"
    locked = Quote("NVDA", 212.85, 212.85, 200, 300, None, fresh)
    assert sp.reference(CTX, locked, LABEL_SCHWAB, NOW).kind == "schwab"


def test_zero_reference_is_absent_and_zero_oracle_gives_a_null_edge():
    bad = replace(REF_SCHWAB, price=0.0, bid=0.0, ask=0.0)
    e = sp.score(replace(CTX, mark=215.0), BOOK, bad)
    assert e.ref_kind == "oracle" and e.reference == CTX.oracle
    e = sp.score(replace(CTX, mark=215.0, oracle=0.0), BOOK, replace(REF_ORACLE, price=0.0))
    assert e.basis_bps == 0.0 and e.net_bps == 0.0 and e.capacity_usd == 0.0 and e.capacity_contracts == 0.0


def test_walk_perp_residue_does_not_take_a_dust_slice():
    lv = [PerpLevel("bid", 0, 0.1, 3.0), PerpLevel("bid", 1, 0.2, 5.0)]
    c, usd, _ = sp.walk_perp(lv, "bid", 0.1 * 3.0)
    assert c == 3.0 and usd == pytest.approx(0.3)


def test_gross_to_net_matches_a_fresh_score_and_rejects_zero_hold():
    for mark in (215.0, 210.0):
        a = sp.score(replace(CTX, mark=mark), BOOK, REF_SCHWAB)
        assert sp.gross_to_net(a, 168) == pytest.approx(sp.score(replace(CTX, mark=mark), BOOK, REF_SCHWAB, hold_h=168).net_bps)
    with pytest.raises(ValueError):
        sp.gross_to_net(replace(a, hold_h=0), 24)


def test_growth_mode_picks_the_fee_tier():
    g = sp.score(replace(CTX, mark=215.0), BOOK, REF_SCHWAB)
    s = sp.score(replace(CTX, mark=215.0, growth_mode=False), BOOK, REF_SCHWAB)
    assert g.perp_fee_bps == pytest.approx(1.8) and s.perp_fee_bps == pytest.approx(18.0)


def test_gross_to_net_rescales_carry_only():
    e = sp.score(replace(CTX, mark=210.0), BOOK, REF_SCHWAB)
    assert sp.gross_to_net(e, e.hold_h) == pytest.approx(e.net_bps)
    assert sp.gross_to_net(e, 168) == pytest.approx(e.net_bps - (e.funding_bps + e.borrow_bps) * (168 / e.hold_h - 1))


def test_episodes_from_edge_rows_use_ten_bps_rule():
    rows = [sp.edge_row(NOW + timedelta(minutes=i), replace(E_OK, net_bps=12.0), "v") for i in range(3)]
    eps = episodes.find(sp.episode_points(rows), NOW + timedelta(days=1), kalshi_id="xyz:NVDA", pm_id="NVDA",
                        direction="short_perp_long_stock", min_edge=10 / 1e4, annualize=sp.annualize)
    assert len(eps) == 1 and eps[0].snapshots == 3 and eps[0].annualized == pytest.approx(12e-4 * 365)
    assert not episodes.find(sp.episode_points([{**rows[0], "net_bps": 9.9}, rows[1]]), NOW, kalshi_id="x", pm_id="y", direction="d", min_edge=1e-3, annualize=sp.annualize)


def test_edge_row_columns():
    r = sp.edge_row(NOW, E_OK, "v")
    assert set(r) == {"ts", "symbol", "direction", "ref_kind", "reference", "mark", "basis_bps", "perp_fee_bps", "perp_spread_bps",
                      "stock_spread_bps", "funding_bps", "borrow_bps", "net_bps", "capacity_contracts",
                      "capacity_usd", "fee_version"}
