"""Pure helpers for the order-book snapshotter: which markets to poll, and the ClickHouse row shape."""

from datetime import datetime

from venue_arb.config import LADDER_CAP, SNAPSHOT_MAX_MARKETS
from venue_arb.models import Level

_RANK = {"exact": 0, "near": 1, None: 2}


def watchlist(rows: list[dict], ladder_markets: list[dict] | None = None, cap: int = SNAPSHOT_MAX_MARKETS,
              ladder_cap: int = LADDER_CAP) -> list[tuple[str, str, str | None]]:
    """(venue, market_id, yes_token): exact pairs, then ladder strikes, then near pairs, then unadjudicated by score.

    Row keys: a_venue, a_id, a_token, b_venue, b_id, b_token, relation (None = unadjudicated), score.
    `ladder_markets` are clipped ladders ({venue: [Strike]}); each contributes at most `ladder_cap` strikes
    (both venues together), nearest to the midpoint of its Polymarket strike range first.
    """
    live = [r for r in rows if r["relation"] != "different"]
    live.sort(key=lambda r: (_RANK[r["relation"]], -(r["score"] or 0.0)))
    out: dict[tuple[str, str], str | None] = {}

    def add(rs):
        for r in rs:
            out.setdefault((r["a_venue"], r["a_id"]), r["a_token"])
            out.setdefault((r["b_venue"], r["b_id"]), r["b_token"])

    add(r for r in live if r["relation"] == "exact")
    for lad in ladder_markets or []:
        levels = [s.level for s in lad["polymarket"]]
        mid = (min(levels) + max(levels)) / 2
        for s in sorted((s for xs in lad.values() for s in xs), key=lambda s: abs(s.level - mid))[:ladder_cap]:
            out.setdefault((s.venue, s.market_id), s.yes_token)
    add(r for r in live if r["relation"] != "exact")
    return [(v, m, t) for (v, m), t in out.items()][:cap]


def to_rows(venue: str, market_id: str, ts: datetime, levels: list[Level]) -> list[dict]:
    return [{"ts": ts, "venue": venue, "market_id": market_id, "side": lv.side, "level": lv.level,
             "price": lv.price, "size": lv.size} for lv in levels]
