"""Score one window of stock-perp snapshots (pure): ctx, book and reference rows in, edge rows and episodes out.

The flow (flows/stock_perp_score.py) reads the rows from ClickHouse; rows are dicts keyed by the table's columns.
"""
from collections import defaultdict
from datetime import datetime, timedelta

from venue_arb import episodes, scoring, stock_perps
from venue_arb.config import STOCK_PERP_HOLD_H, STOCK_PERP_MIN_NET_BPS
from venue_arb.edges import Edge
from venue_arb.fees import FEE_VERSION
from venue_arb.sources.hyperliquid import Label, PerpCtx, PerpLevel, label_or_none
from venue_arb.sources.schwab import Quote


def score_window(ctx_rows: list[dict], book_rows: list[dict], ref_rows: list[dict], labels: dict[str, Label], *,
                 start: datetime, end: datetime) -> tuple[list[dict], list[episodes.Episode], dict]:
    """One edge row per (symbol, ctx tick) in [start, end) that has book rows at that tick. The reference is the
    `schwab` ref row with the same ts (the collector stamps ctx, books and refs with one tick time); a missing, stale
    or one-sided one scores against the oracle. `schwab_daily` history rows are ignored. Episodes: one run per
    (symbol, direction); a tick scored in the other direction is a non-qualifying point there, so it ends the run."""
    books: dict[tuple[str, datetime], list[PerpLevel]] = defaultdict(list)
    for b in book_rows:
        books[b["symbol"], b["ts"]].append(PerpLevel(b["side"], b["level"], b["price"], b["size"]))
    refs = {(r["symbol"], r["ts"]): r for r in ref_rows if r["ref_kind"] == "schwab"}
    rows: list[dict] = []
    snapshots = 0
    for c in sorted((c for c in ctx_rows if start <= c["ts"] < end), key=lambda c: (c["symbol"], c["ts"])):
        snapshots += 1
        book = books.get((c["symbol"], c["ts"]))
        if not book:
            continue
        ctx = PerpCtx(c["symbol"], c["mark"], c["oracle"], c["mid"], c["funding_h"], c["premium"], c["open_interest"],
                      c["day_volume_usd"], bool(c["growth_mode"]), 0)
        r = refs.get((c["symbol"], c["ts"]))
        quote = Quote(r["ref_symbol"], r["bid"], r["ask"], r["bid_size"], r["ask_size"], r["last"], r["quote_time"]) if r else None
        ref = stock_perps.reference(ctx, quote, label_or_none(c["symbol"], labels), c["ts"])
        rows.append(stock_perps.edge_row(c["ts"], stock_perps.score(ctx, book, ref), FEE_VERSION))
    by_symbol: dict[str, list[dict]] = defaultdict(list)
    for r in rows:
        by_symbol[r["symbol"]].append(r)
    close_time = end + timedelta(hours=STOCK_PERP_HOLD_H)
    eps = []
    for symbol, g in sorted(by_symbol.items()):
        for d in sorted({r["direction"] for r in g}):
            pts = stock_perps.episode_points([r for r in g if r["direction"] == d]) + [
                (r["ts"], Edge(d, None, 0.0, 0.0, None)) for r in g if r["direction"] != d]
            eps += episodes.find(pts, close_time, kalshi_id=symbol, pm_id=label_or_none(symbol, labels).ref_symbol or symbol,
                                 direction=d, min_edge=STOCK_PERP_MIN_NET_BPS / 1e4, annualize=stock_perps.annualize)
    stats = {"symbols": len({c["symbol"] for c in ctx_rows}), "snapshots": snapshots, "edge_rows": len(rows),
             "rows_with_capacity": sum(r["capacity_contracts"] > 0 for r in rows), "episodes": len(eps),
             "oracle_only_rows": sum(r["ref_kind"] == "oracle" for r in rows)}
    return rows, eps, stats


def stock_perp_opportunity_row(ep: episodes.Episode) -> dict:
    """An `opportunities` row for a stock-perp episode: kalshi_id is the perp, pm_id the stock (the episode carries
    both); never US-executable (Hyperliquid perps), and no transfer between venues is modelled."""
    return {**scoring.opportunity_row(ep, "stock_perp"), "us_executable": False, "transfer_cost_usd": 0.0}
