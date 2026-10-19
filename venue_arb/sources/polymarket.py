import json
import logging
from datetime import datetime

from venue_arb import http
from venue_arb.models import Level, Listing

GAMMA = "https://gamma-api.polymarket.com"
CLOB = "https://clob.polymarket.com"

log = logging.getLogger(__name__)


def list_events(tag_slugs: list[str], get=http.get_json, page: int = 100) -> list[dict]:
    events = {}
    for tag in tag_slugs:
        offset = 0
        while True:
            params = {"active": "true", "closed": "false", "tag_slug": tag, "limit": page, "offset": offset}
            rows = get(f"{GAMMA}/events", params=params)
            for e in rows:
                events.setdefault(e["id"], e)
            if len(rows) < page:
                break
            offset += page
    return list(events.values())


def parse_listings(events: list[dict]) -> list[Listing]:
    out, skipped = [], 0
    for e in events:
        ev = {k: v for k, v in e.items() if k != "markets"}
        for m in e.get("markets", []):
            if not m.get("enableOrderBook") or m.get("closed"):
                continue
            try:
                outcomes, tokens = json.loads(m["outcomes"]), json.loads(m["clobTokenIds"])
                end = datetime.fromisoformat(m["endDate"])
                (yes_label, no_label), (yes_token, no_token) = outcomes, tokens
            except (KeyError, TypeError, ValueError):  # missing/malformed field, or not exactly 2 outcomes/tokens
                skipped += 1
                continue
            out.append(Listing(
                venue="polymarket", market_id=m["conditionId"], event_id=e["id"], category=None,
                title=m["question"], rules=m["description"], yes_label=yes_label, no_label=no_label,
                yes_token=yes_token, no_token=no_token,
                close_time=end,
                resolution_source=m.get("resolutionSource") or None, raw={"event": ev, "market": m},
            ))
    if skipped:
        log.warning("skipped %d malformed polymarket markets", skipped)
    return out


def fetch_book(token_id: str, get=http.get_json) -> dict:
    return get(f"{CLOB}/book", params={"token_id": token_id})


def parse_book(payload: dict, depth: int = 10) -> list[Level]:
    # ponytail: one book call per market by mirroring the token-0 book; fetch the token-1 book if a spot check
    # disagrees by more than one tick.
    asks = sorted((float(a["price"]), float(a["size"])) for a in payload.get("asks") or [])[:depth]
    bids = sorted(((float(b["price"]), float(b["size"])) for b in payload.get("bids") or []), reverse=True)[:depth]
    return [Level("yes", i, p, s) for i, (p, s) in enumerate(asks)] + [
        Level("no", i, round(1 - p, 4), s) for i, (p, s) in enumerate(bids)
    ]
