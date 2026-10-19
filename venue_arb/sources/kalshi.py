from datetime import datetime

from venue_arb import http
from venue_arb.models import Level, Listing

API = "https://api.elections.kalshi.com/trade-api/v2"


def list_events(series_tickers: list[str], get=http.get_json) -> list[dict]:
    events = []
    for series in series_tickers:
        params = {"status": "open", "with_nested_markets": "true", "limit": 200, "series_ticker": series}
        while True:
            page = get(f"{API}/events", params=params)
            events += page.get("events", [])
            if not page.get("cursor"):
                break
            params = {**params, "cursor": page["cursor"]}
    return events


def parse_listings(events: list[dict]) -> list[Listing]:
    out = []
    for e in events:
        ev = {k: v for k, v in e.items() if k != "markets"}
        src = next((s["url"] for s in e.get("settlement_sources") or [] if s.get("url")), None)
        for m in e.get("markets", []):
            out.append(Listing(
                venue="kalshi", market_id=m["ticker"], event_id=e["event_ticker"], category=None,
                title=m["title"], rules=m["rules_primary"] + "\n" + m.get("rules_secondary", ""),
                yes_label=m["yes_sub_title"], no_label=m.get("no_sub_title") or "No",
                yes_token=None, no_token=None,
                close_time=datetime.fromisoformat(m["close_time"]),  # "Z" -> UTC-aware on 3.11+
                resolution_source=src, raw={"event": ev, "market": m},
            ))
    return out


def fetch_book(ticker: str, get=http.get_json) -> dict:
    return get(f"{API}/markets/{ticker}/orderbook", params={"depth": 10})


def parse_book(payload: dict, depth: int = 10) -> list[Level]:
    book = payload.get("orderbook_fp") or {}
    out = []
    # a bid on one outcome at p is an ask on the other at 1 - p
    for side, bids in (("yes", book.get("no_dollars") or []), ("no", book.get("yes_dollars") or [])):
        asks = sorted((round(1 - float(p), 4), float(s)) for p, s in bids)[:depth]
        out += [Level(side, i, p, s) for i, (p, s) in enumerate(asks)]
    return out
