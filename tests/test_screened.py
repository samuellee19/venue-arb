import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

from venue_arb.exact import ExactPair
from venue_arb.screened import gaps
from venue_arb.sources import kalshi, polymarket

FX = Path(__file__).parent / "fixtures"


def load(name):
    return json.loads((FX / name).read_text())


def test_kalshi_candles_parse_and_historical_fallback():
    calls = []

    def live(url, params=None, **kw):
        calls.append(url)
        return load("kalshi_candles.json")

    out = kalshi.fetch_candles("S", "S-T", 1792270000, 1792281600, get=live)
    assert out[0] == {"ts": datetime.fromtimestamp(1792274400, UTC), "yes_bid": 0.86, "yes_ask": 0.87}
    assert out[2]["yes_bid"] is None and out[2]["yes_ask"] is None
    assert len(out) == 3 and calls == [f"{kalshi.API}/series/S/markets/S-T/candlesticks"]

    calls.clear()

    def hist(url, params=None, **kw):
        calls.append(url)
        return {} if "/series/" in url else load("kalshi_candles_historical.json")

    out = kalshi.fetch_candles("S", "S-T", 1787180000, 1787190000, get=hist)
    assert calls == [f"{kalshi.API}/series/S/markets/S-T/candlesticks", f"{kalshi.API}/historical/markets/S-T/candlesticks"]
    assert out == [{"ts": datetime.fromtimestamp(1787183220, UTC),
                    "yes_bid": None, "yes_ask": None}]


def test_kalshi_candles_chunk_60_days():
    seen = []

    def fake(url, params=None, **kw):
        seen.append((params["start_ts"], params["end_ts"]))
        return {"candlesticks": [{"end_period_ts": 7200}]}

    kalshi.fetch_candles("S", "T", 0, 130 * 86400, get=fake)
    assert len(seen) == 3 and all(e - s <= 60 * 86400 for s, e in seen) and seen[1][0] == seen[0][1]


def test_pm_history_parse():
    out = polymarket.fetch_price_history("tok", 1792188000, 1792200000, get=lambda *a, **k: load("pm_history.json"))
    assert out[0] == {"ts": datetime.fromtimestamp(1792188027, UTC), "price": 0.0065}
    assert len(out) == 3


def pair(maps="yes"):
    return ExactPair("K", "P", "tok", maps, datetime(2027, 1, 1, tzinfo=UTC), "S", None, False, 0.9, False)


H = [datetime(2026, 10, 19, h, tzinfo=UTC) for h in range(4)]
FEE = {"fee_type": "quadratic", "fee_multiplier": 1}


def test_gaps_directions_and_mapping():
    k = [{"ts": H[0], "yes_bid": 0.30, "yes_ask": 0.40}]
    pm = [{"ts": H[0].replace(minute=20), "price": 0.50}]  # truncated to the hour
    r = {g["direction"]: g for g in gaps(pair("yes"), k, pm, FEE)}
    assert r["k_yes_pm_no"]["k_price"] == 0.40 and r["k_yes_pm_no"]["pm_price"] == 0.50
    assert abs(r["k_yes_pm_no"]["gross_gap"] - 0.10) < 1e-9
    assert abs(r["k_no_pm_yes"]["k_price"] - 0.70) < 1e-9 and r["k_no_pm_yes"]["pm_price"] == 0.50
    assert r["k_yes_pm_no"]["ts"] == H[0] and r["k_yes_pm_no"]["net_gap_top"] < r["k_yes_pm_no"]["gross_gap"]
    r = {g["direction"]: g for g in gaps(pair("no"), k, pm, FEE)}  # flipped Polymarket leg
    assert r["k_yes_pm_no"]["pm_price"] == 0.50 and r["k_no_pm_yes"]["pm_price"] == 0.50
    pm = [{"ts": H[0], "price": 0.2}]
    r = {g["direction"]: g for g in gaps(pair("no"), k, pm, FEE)}
    assert r["k_yes_pm_no"]["pm_price"] == 0.2 and abs(r["k_no_pm_yes"]["pm_price"] - 0.8) < 1e-9


def test_gaps_skip_missing_hours():
    k = [{"ts": H[0], "yes_bid": 0.3, "yes_ask": 0.4}, {"ts": H[1], "yes_bid": None, "yes_ask": None},
         {"ts": H[2], "yes_bid": 0.3, "yes_ask": 0.4}]
    pm = [{"ts": H[0], "price": 0.5}, {"ts": H[1], "price": 0.5}, {"ts": H[3], "price": 0.5}]
    assert {g["ts"] for g in gaps(pair(), k, pm, FEE)} == {H[0]}


def test_gaps_skip_empty_book_hours():
    k = [{"ts": H[0], "yes_bid": None, "yes_ask": None}]
    assert gaps(pair(), k, [{"ts": H[0], "price": 0.5}], FEE) == []


def test_gaps_numeric_asymmetric_with_fees():
    from venue_arb.fees import kalshi_taker_fee

    k = [{"ts": H[0], "yes_bid": 0.30, "yes_ask": 0.40}]
    r = {g["direction"]: g for g in gaps(pair("yes"), k, [{"ts": H[0], "price": 0.30}], FEE)}
    a, b = r["k_yes_pm_no"], r["k_no_pm_yes"]
    assert (a["k_price"], a["pm_price"]) == (0.40, 0.70) and abs(a["gross_gap"] - (-0.10)) < 1e-9
    assert abs(b["k_price"] - 0.70) < 1e-9 and b["pm_price"] == 0.30 and abs(b["gross_gap"]) < 1e-9
    assert abs(a["net_gap_top"] - (a["gross_gap"] - kalshi_taker_fee(0.40, 1, "quadratic", 1))) < 1e-9
    assert abs(a["net_gap_top"] - (-0.10 - 0.02)) < 1e-9  # ceil(7 * .4 * .6 = 1.68c) = 2c, Polymarket fees off


def test_kalshi_candles_fall_back_on_empty_live_page():
    def fake(url, params=None, **kw):
        return {"candlesticks": []} if "/series/" in url else load("kalshi_candles_historical.json")

    assert len(kalshi.fetch_candles("S", "T", 1787180000, 1787190000, get=fake)) == 1


def test_gaps_pair_kalshi_close_with_nearest_pm_print():
    # Kalshi's close for the hour ending H1 is the quote AT H1; Polymarket prints land ~27 s past each hour. The old
    # hour-floor pairing put that close at H0 against the H0:00:27 print (0.40); it must meet the H1:00:27 one (0.60).
    h1 = int(H[1].timestamp())
    k = kalshi.fetch_candles("S", "T", h1 - 3600, h1, get=lambda *a, **kw: {"candlesticks": [
        {"end_period_ts": h1, "yes_bid": {"close_dollars": "0.30"}, "yes_ask": {"close_dollars": "0.40"}}]})
    pm = [{"ts": H[0].replace(second=27), "price": 0.40}, {"ts": H[1].replace(second=27), "price": 0.60}]
    r = {g["direction"]: g for g in gaps(pair(), k, pm, FEE)}
    assert r["k_no_pm_yes"]["ts"] == H[1] and r["k_no_pm_yes"]["pm_price"] == 0.60


def test_gaps_nearest_print_within_30_min():
    k = [{"ts": H[1], "yes_bid": 0.3, "yes_ask": 0.4}]
    near = lambda m, p: {"ts": H[1] + timedelta(minutes=m), "price": p}
    assert gaps(pair(), k, [near(-50, 0.1), near(25, 0.2)], FEE)[0]["pm_price"] == 0.8  # k_yes_pm_no buys pm No
    assert gaps(pair(), k, [near(-30, 0.1), near(30, 0.2)], FEE)[0]["pm_price"] == 0.9  # tie: earlier print
    assert gaps(pair(), k, [near(-31, 0.1), near(31, 0.2)], FEE) == []
