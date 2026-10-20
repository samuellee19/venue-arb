"""Pure helpers for the order-book snapshotter: which markets to poll, and the ClickHouse row shape."""

import logging
from datetime import datetime

from venue_arb.config import LADDER_CAP, SNAPSHOT_MAX_MARKETS
from venue_arb.models import Level

log = logging.getLogger("venue_arb.books")
_RANK = {"exact": 0, "near": 1, None: 2}


def watchlist(rows: list[dict], ladder_markets: list[dict] | None = None, cap: int = SNAPSHOT_MAX_MARKETS,
              ladder_cap: int = LADDER_CAP) -> list[tuple[str, str, str | None]]:
    """(venue, market_id, yes_token): exact pairs, then ladder strikes, then near pairs, then unadjudicated by score.

    Row keys: a_venue, a_id, a_token, b_venue, b_id, b_token, relation (None = unadjudicated), score.
    `ladder_markets` are clipped ladders ({venue: [Strike]}); each keeps every Polymarket strike plus the Kalshi strikes
    nearest to the midpoint of the Polymarket range, filling up to `ladder_cap` strikes in all.
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
        pm = lad["polymarket"]
        mid = (min(s.level for s in pm) + max(s.level for s in pm)) / 2
        ks = sorted(lad["kalshi"], key=lambda s: abs(s.level - mid))
        keep = ks[:max(0, ladder_cap - len(pm))]
        log.info("ladder %s %s: polymarket %d kept, kalshi %d kept %d trimmed", pm[0].asset, pm[0].instant,
                 len(pm), len(keep), len(ks) - len(keep))
        for s in [*pm, *keep]:
            out.setdefault((s.venue, s.market_id), s.yes_token)
    add(r for r in live if r["relation"] != "exact")
    full = [(v, m, t) for (v, m), t in out.items()]
    if len(full) > cap:
        log.info("watchlist cap %d displaced %d legs (lowest-ranked near/unadjudicated)", cap, len(full) - cap)
    return full[:cap]


def to_rows(venue: str, market_id: str, ts: datetime, levels: list[Level]) -> list[dict]:
    return [{"ts": ts, "venue": venue, "market_id": market_id, "side": lv.side, "level": lv.level,
             "price": lv.price, "size": lv.size} for lv in levels]
