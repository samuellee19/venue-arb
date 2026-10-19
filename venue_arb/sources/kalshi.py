import logging
from datetime import UTC, datetime

from venue_arb import http
from venue_arb.models import Level, Listing

API = "https://api.elections.kalshi.com/trade-api/v2"
log = logging.getLogger(__name__)


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
    out, skipped = [], 0
    for e in events:
        ev = {k: v for k, v in e.items() if k != "markets"}
        src = next((s["url"] for s in e.get("settlement_sources") or [] if s.get("url")), None)
        for m in e.get("markets") or []:
            try:
                if not m["title"]:
                    raise ValueError("blank title")
                out.append(Listing(
                    venue="kalshi", market_id=m["ticker"], event_id=e["event_ticker"], category=None,
                    title=m["title"],
                    rules=(m.get("rules_primary") or "") + "\n" + (m.get("rules_secondary") or ""),
                    yes_label=m["yes_sub_title"] or "", no_label=m.get("no_sub_title") or "No",
                    yes_token=None, no_token=None,
                    close_time=datetime.fromisoformat(m["close_time"]),  # "Z" -> UTC-aware on 3.11+
                    resolution_source=src, raw={"event": ev, "market": m},
                ))
            except (KeyError, TypeError, ValueError):  # missing/null required field or bad close_time
                skipped += 1
    if skipped:
        log.warning("skipped %d malformed kalshi markets", skipped)
    return out


def fetch_series(ticker: str, get=http.get_json) -> dict:
    return get(f"{API}/series/{ticker}").get("series", {})


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


CHUNK_S = 60 * 86400


def _price(side: dict | None) -> float | None:
    # live fields are *_dollars, the historical endpoint's are bare
    v = (side or {}).get("close_dollars", (side or {}).get("close"))
    return None if v is None else float(v)


def _quote(side: dict | None, empty: float) -> float | None:
    # an empty side shows as ask 1.0 / bid 0.0: no executable price
    v = _price(side)
    return None if v == empty else v


def fetch_candles(series: str, ticker: str, start_ts: int, end_ts: int, get=http.get_json) -> list[dict]:
    """Hourly yes bid/ask closes, keyed by `end_period_ts`: Kalshi documents a candle's close as the offer "at the
    end of the candlestick period" (docs/venues.md), so that is the instant the quote applies."""
    out = []
    for lo in range(start_ts, end_ts, CHUNK_S):
        params = {"start_ts": lo, "end_ts": min(lo + CHUNK_S, end_ts), "period_interval": 60}
        page = get(f"{API}/series/{series}/markets/{ticker}/candlesticks", params=params)
        if not page or not page.get("candlesticks"):  # 404 or empty: settled before the live cutoff
            page = get(f"{API}/historical/markets/{ticker}/candlesticks", params=params)
        for c in page.get("candlesticks") or []:
            ts = datetime.fromtimestamp(c["end_period_ts"], UTC)
            out.append({"ts": ts, "yes_bid": _quote(c.get("yes_bid"), 0.0), "yes_ask": _quote(c.get("yes_ask"), 1.0)})
    return out
