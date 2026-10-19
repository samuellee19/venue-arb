import json
from pathlib import Path

from venue_arb.models import Level
from venue_arb.sources import kalshi

FX = Path(__file__).parent / "fixtures"


def load(name):
    return json.loads((FX / name).read_text())


def test_list_events_follows_cursor():
    p1, p2 = load("kalshi_events_p1.json"), load("kalshi_events_p2.json")
    assert p1["cursor"] and not p2["cursor"]
    calls = []

    def fake(url, params=None, **kw):
        calls.append(dict(params))
        return p2 if "cursor" in params else p1

    out = kalshi.list_events(["KXFEDDECISION"], get=fake)
    assert len(calls) == 2
    assert calls[1]["cursor"] == p1["cursor"]
    assert out == p1["events"] + p2["events"]


def test_parse_listings_fields():
    p1 = load("kalshi_events_p1.json")
    ls = kalshi.parse_listings(p1["events"])
    m = p1["events"][0]["markets"][0]
    first = ls[0]
    assert first.market_id == m["ticker"]
    assert first.event_id == p1["events"][0]["event_ticker"]
    assert first.yes_label == m["yes_sub_title"]
    assert first.close_time.tzinfo is not None
    assert first.category is None
    assert first.rules == m["rules_primary"] + "\n" + m["rules_secondary"]
    assert first.resolution_source == "https://www.federalreserve.gov"
    assert "markets" not in first.raw["event"] and first.raw["market"] == m
    assert first.raw["event"]["series_ticker"] == "KXFEDDECISION"


def test_parse_book_derives_asks():
    payload = {"orderbook_fp": {"no_dollars": [["0.97", "2030.01"], ["0.98", "756579.01"]], "yes_dollars": []}}
    lv = kalshi.parse_book(payload)
    assert lv == [Level("yes", 0, 0.02, 756579.01), Level("yes", 1, 0.03, 2030.01)]


def test_parse_book_empty_side():
    lv = kalshi.parse_book(load("kalshi_book_empty_yes.json"))
    assert lv and not [x for x in lv if x.side == "no"]
    assert all(0 < x.price < 1 for x in lv)
    assert kalshi.parse_book(load("kalshi_book.json"))


def test_parse_listings_skips_malformed_and_handles_nulls():
    ev = load("kalshi_events_p1.json")["events"][0]
    good = ev["markets"][0]
    no_title = {k: v for k, v in good.items() if k != "title"} | {"ticker": "NO-TITLE"}
    null_rules = good | {"ticker": "NULL-RULES", "rules_secondary": None, "rules_primary": None}
    bad_close = good | {"ticker": "BAD-CLOSE", "close_time": None}
    ls = kalshi.parse_listings([ev | {"markets": [no_title, null_rules, bad_close]}])
    assert [x.market_id for x in ls] == ["NULL-RULES"]
    assert ls[0].rules == "\n"
    assert kalshi.parse_listings([ev | {"markets": None}]) == []
