"""Pure helpers for the order-book snapshotter: which markets to poll, and the ClickHouse row shape."""

from datetime import datetime

from venue_arb.config import SNAPSHOT_MAX_MARKETS
from venue_arb.models import Level

_RANK = {"exact": 0, "near": 1, None: 2}


def watchlist(rows: list[dict], cap: int = SNAPSHOT_MAX_MARKETS) -> list[tuple[str, str, str | None]]:
    """(venue, market_id, yes_token) for both legs of each pair: exact, then near, then unadjudicated by score.

    Row keys: a_venue, a_id, a_token, b_venue, b_id, b_token, relation (None = unadjudicated), score.
    """
    live = [r for r in rows if r["relation"] != "different"]
    live.sort(key=lambda r: (_RANK[r["relation"]], -(r["score"] or 0.0)))
    out: dict[tuple[str, str], str | None] = {}
    for r in live:
        out.setdefault((r["a_venue"], r["a_id"]), r["a_token"])
        out.setdefault((r["b_venue"], r["b_id"]), r["b_token"])
    return [(v, m, t) for (v, m), t in out.items()][:cap]


def to_rows(venue: str, market_id: str, ts: datetime, levels: list[Level]) -> list[dict]:
    return [{"ts": ts, "venue": venue, "market_id": market_id, "side": lv.side, "level": lv.level,
             "price": lv.price, "size": lv.size} for lv in levels]
