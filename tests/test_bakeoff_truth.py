from datetime import UTC, date, datetime

from venue_arb.bakeoff.truth import crypto_pairs, parse


def k(mid, title, yes, close=datetime(2026, 10, 20, 21, tzinfo=UTC), event=None):
    return {"venue": "kalshi", "market_id": mid, "event_id": event or mid.rsplit("-", 1)[0], "title": title,
            "yes_label": yes, "close_time": close, "category": "crypto_price"}


def p(mid, title, close=datetime(2026, 10, 20, 16, tzinfo=UTC), yes="Yes"):
    return {"venue": "polymarket", "market_id": mid, "event_id": "1", "title": title, "yes_label": yes,
            "close_time": close, "category": "crypto_price"}


D = date(2026, 10, 20)


def test_parse_kalshi():
    assert parse(k("KXBTCD-26OCT2017-T89999.99", "Bitcoin price on Oct 20, 2026?", "$90,000 or above")) == \
        ("BTC", D, "above", 90000.0, None)
    assert parse(k("KXETH-26OCT2003-B1787", "Ethereum price at Oct 20, 2026 at 3am EDT?", "$1,785 to 1,789.99",
                   close=datetime(2026, 10, 20, 7, tzinfo=UTC))) == ("ETH", D, "range", 1785.0, 1790.0)
    assert parse(k("KXBTC-26OCT2017-B1", "Bitcoin price range on Oct 20, 2026?", "$70,000 or below"))[2:] == \
        ("below", 70000.0, None)


def test_parse_polymarket():
    assert parse(p("a", "Will the price of Bitcoin be above $80,000 on October 20?")) == \
        ("BTC", D, "above", 80000.0, None)
    assert parse(p("a", "Will the price of Ethereum be greater than $3,100 on October 20?"))[2:] == \
        ("above", 3100.0, None)
    assert parse(p("a", "Will the price of Bitcoin be less than $74,000 on October 20?"))[2:] == \
        ("below", 74000.0, None)
    assert parse(p("a", "Will the price of Bitcoin be between $82,000 and $84,000 on October 20?"))[2:] == \
        ("range", 82000.0, 84000.0)
    # hourly ladder; date is the ET date of close_time (2AM ET = 06:00 UTC)
    assert parse(p("a", "Bitcoin above 84,200 on October 20, 2AM ET?", close=datetime(2026, 10, 20, 6, tzinfo=UTC))) \
        == ("BTC", D, "above", 84200.0, None)


def test_parse_rejects_other_shapes():
    for t in ("Bitcoin Up or Down - October 20, 2:50AM-2:55AM ET", "Will Bitcoin reach $90,000 by October 31?",
              "Will Bitcoin dip to $80,000 on October 20?", "Will the price of Solana be above $200 on October 20?"):
        assert parse(p("a", t)) is None
    assert parse(p("a", "Will the price of Bitcoin be above $80,000 on October 20?", yes="No")) is None


def test_crypto_pairs_same_asset_date_direction_threshold():
    ks = [k("KXBTCD-26OCT2017-T79999.99", "Bitcoin price on Oct 20, 2026?", "$80,000 or above"),
          k("KXBTCD-26OCT2017-T80499.99", "Bitcoin price on Oct 20, 2026?", "$80,500 or above"),
          k("KXBTC-26OCT2017-B81000", "Bitcoin price range on Oct 20, 2026?", "$80,000 to 81,999.99")]
    ps = [p("hit", "Will the price of Bitcoin be above $80,000 on October 20?"),
          p("eth", "Will the price of Ethereum be above $80,000 on October 20?"),
          p("day", "Will the price of Bitcoin be above $80,000 on October 21?",
            close=datetime(2026, 10, 21, 16, tzinfo=UTC)),
          p("rng", "Will the price of Bitcoin be between $80,000 and $82,000 on October 20?"),
          p("below", "Will the price of Bitcoin be less than $80,000 on October 20?")]
    assert crypto_pairs(ks, ps) == [("KXBTCD-26OCT2017-T79999.99", "hit", "crypto_above"),
                                    ("KXBTC-26OCT2017-B81000", "rng", "crypto_range")]

