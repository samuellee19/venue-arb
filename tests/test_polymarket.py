import json
from pathlib import Path

from venue_arb.models import Level
from venue_arb.sources import polymarket

FX = Path(__file__).parent / "fixtures"


def load(name):
    return json.loads((FX / name).read_text())


def test_list_events_follows_offset():
    p1, p2 = load("pm_events_p1.json"), load("pm_events_p2.json")
    pages = {0: p1, 2: p2[:1]}
    offsets = []

    def fake(url, params=None, **kw):
        offsets.append(params["offset"])
        return pages[params["offset"]]

    out = polymarket.list_events(["fed"], get=fake, page=2)
    assert offsets == [0, 2]
    assert len(out) == 3


def test_list_events_dedupes_across_tags():
    p1 = load("pm_events_p1.json")
    out = polymarket.list_events(["fed", "economy"], get=lambda url, params=None, **kw: p1, page=100)
    assert [e["id"] for e in out] == [e["id"] for e in p1]


def test_parse_listings_fields():
    p1 = load("pm_events_p1.json")
    ls = polymarket.parse_listings(p1)
    m = p1[0]["markets"][0]
    first = ls[0]
    assert first.market_id == m["conditionId"] and first.event_id == p1[0]["id"]
    assert first.title == m["question"] and first.rules == m["description"]
    assert first.close_time.tzinfo is not None
    assert first.resolution_source == (m.get("resolutionSource") or None)
    assert "markets" not in first.raw["event"] and first.raw["market"] == m
    assert all("slug" in t for t in first.raw["event"]["tags"])


def test_parse_listings_non_yes_no_outcomes():
    ev = load("pm_events_teams.json")
    m = ev[0]["markets"][0]
    first = polymarket.parse_listings(ev)[0]
    outcomes, tokens = json.loads(m["outcomes"]), json.loads(m["clobTokenIds"])
    assert outcomes != ["Yes", "No"]
    assert (first.yes_label, first.no_label) == tuple(outcomes)
    assert (first.yes_token, first.no_token) == tuple(tokens)


def test_parse_book_mirrors_no_side():
    payload = {"bids": [{"price": "0.97", "size": "10"}], "asks": [{"price": "0.972", "size": "5"}]}
    assert polymarket.parse_book(payload) == [Level("yes", 0, 0.972, 5.0), Level("no", 0, 0.03, 10.0)]


def test_parse_book_sorts_best_first():
    lv = polymarket.parse_book(load("pm_book.json"))
    yes = [x.price for x in lv if x.side == "yes"]
    no = [x.price for x in lv if x.side == "no"]
    assert yes == sorted(yes) and no == sorted(no) and yes and no
