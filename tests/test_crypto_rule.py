import json
from datetime import UTC, datetime
from pathlib import Path

from venue_arb.matching.crypto_rule import clip, ladders, parse, parse_strike

H5 = datetime(2026, 10, 20, 9, tzinfo=UTC)  # 5 AM EDT


def rows():
    out = json.loads((Path(__file__).parent / "fixtures" / "crypto_listings.json").read_text())
    for r in out:
        r["close_time"] = datetime.fromisoformat(r["close_time"])
    return out


def by_title(t):
    return next(r for r in rows() if r["title"] == t)


def k(level, close=H5, asset="KXBTCD", label=None):
    return {"venue": "kalshi", "market_id": f"{asset}-{level}", "event_id": f"{asset}-26OCT2005", "category": "crypto_price",
            "title": "x", "yes_label": label or f"${level:,} or above", "yes_token": None, "close_time": close}


def p(level, close=H5, hour="5AM"):
    return {"venue": "polymarket", "market_id": f"pm{level}", "event_id": "1", "category": "crypto_price",
            "title": f"Bitcoin above {level:,} on October 20, {hour} ET?", "yes_label": "Yes", "yes_token": f"t{level}",
            "close_time": close}


def test_parse_real_listings():
    s = parse_strike(by_title("Bitcoin above 80,000 on October 20, 5AM ET?"))
    assert (s.venue, s.asset, s.instant, s.op, s.level) == ("polymarket", "BTC", H5, "gt", 80000.0)
    assert s.yes_token
    s = parse_strike(by_title("Ethereum above 2,350 on October 20, 5AM ET?"))
    assert (s.asset, s.level) == ("ETH", 2350.0)
    s = parse_strike(by_title("Will the price of Bitcoin be above $80,000 on October 20?"))
    assert s.instant == datetime(2026, 10, 20, 16, tzinfo=UTC) and s.level == 80000.0  # noon EDT
    # "greater than" tail: a value exactly on the bracket edge resolves to the higher bracket, so it is >= X
    s = parse_strike(by_title("Will the price of Bitcoin be greater than $92,000 on October 20?"))
    assert s.level == 91999.99
    kal = next(r for r in rows() if r["market_id"] == "KXBTCD-26OCT2005-T79999.99")
    s = parse_strike(kal)
    assert (s.venue, s.asset, s.instant, s.yes_token, s.level) == ("kalshi", "BTC", H5, None, 79999.99)
    assert parse_strike(next(r for r in rows() if r["market_id"] == "KXBTC-26OCT2005-T91799.99")).level == 91799.99


def test_parse_rejects_other_shapes():
    for t in ("Will the price of Bitcoin be less than $74,000 on October 20?",
              "Will the price of Bitcoin be between $86,000 and $88,000 on October 20?",
              "Bitcoin Up or Down - October 20, 5AM ET", "Will Bitcoin reach $89,000 on October 20?"):
        assert parse_strike(by_title(t)) is None
    assert parse_strike(k(1, label="$73,199.99 or below")) is None
    assert parse_strike(k(1, label="$73,200 to 73,299.99")) is None
    assert parse_strike({**p(80000), "yes_label": "No"}) is None


def test_threshold_semantics_boundary():
    kal, pm = parse_strike(k(80000)), parse_strike(p(80000))
    assert kal.level == 79999.99 and pm.level == 80000.0
    assert kal.level < pm.level  # Kalshi >= 80,000 is the weaker (higher-probability) claim


def test_ladders_only_same_instant():
    h4 = datetime(2026, 10, 20, 8, tzinfo=UTC)
    out = ladders([k(80000), p(80000), k(80100, close=h4), p(80100, close=h4, hour="4AM")])
    assert set(out) == {("BTC", H5), ("BTC", h4)}
    assert [s.market_id for s in out[("BTC", H5)]["kalshi"]] == ["KXBTCD-80000"]
    assert [s.market_id for s in out[("BTC", h4)]["polymarket"]] == ["pm80100"]
    assert len(ladders([k(80000), p(80000, close=datetime(2026, 10, 20, 8, tzinfo=UTC), hour="4AM")])) == 0


def test_ladders_need_both_venues():
    assert ladders([k(80000), k(80100)]) == {}
    assert ladders([p(80000)]) == {}
    out = ladders([k(80100), k(80000), p(81000), p(80500)])[("BTC", H5)]
    assert [s.level for s in out["kalshi"]] == [79999.99, 80099.99]
    assert [s.level for s in out["polymarket"]] == [80500.0, 81000.0]


def test_ladders_real_fixture():
    out = ladders(rows())
    assert set(out) == {("BTC", H5), ("ETH", H5)}  # the 5 PM Kalshi strike and the noon PM daily never pair


def test_clip():
    lad = ladders([k(x) for x in (79000, 79900, 80000, 80600, 81500)] + [p(80000), p(80500)])[("BTC", H5)]
    out = clip(lad, 150)
    assert [s.level for s in out["kalshi"]] == [79899.99, 79999.99, 80599.99]
    assert out["polymarket"] == lad["polymarket"]


def test_bad_dates_return_none_not_raise():
    for t in ("Bitcoin above 80,000 on Octember 20, 5AM ET?", "Bitcoin above 80,000 on February 30, 5AM ET?",
              "Bitcoin above 80,000 on October 20, 13AM ET?", "Bitcoin above 80,000 on October 20, 0AM ET?",
              "Will the price of Bitcoin be above $80,000 on February 30?"):
        assert parse_strike({**p(80000), "title": t}) is None
        parse({**p(80000), "title": t})
