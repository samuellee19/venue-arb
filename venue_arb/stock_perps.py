"""Stock-perp basis against the stock, round-trip costs over a stated hold, and the capacity the stock side allows (pure)."""
from dataclasses import dataclass
from datetime import datetime, timedelta
from statistics import median
from typing import Literal

from venue_arb.config import REF_MAX_AGE_S, STOCK_PERP_BORROW_BPS_YR, STOCK_PERP_HOLD_H
from venue_arb.edges import Edge
from venue_arb.fees import HYPERLIQUID_TAKER
from venue_arb.sources.hyperliquid import Label, PerpCtx, PerpLevel
from venue_arb.sources.schwab import Quote

Direction = Literal["short_perp_long_stock", "long_perp_short_stock"]


@dataclass(frozen=True)
class Reference:
    kind: Literal["schwab", "oracle"]
    symbol: str
    price: float
    bid: float | None
    ask: float | None
    bid_size: float | None
    ask_size: float | None
    asof: datetime


@dataclass(frozen=True)
class StockPerpEdge:
    symbol: str
    direction: Direction
    ref_kind: str
    reference: float
    mark: float
    basis_bps: float
    perp_fee_bps: float
    stock_spread_bps: float | None
    funding_bps: float  # signed cost: negative when the position receives funding
    borrow_bps: float
    net_bps: float
    capacity_contracts: float
    capacity_usd: float
    stock_cap_usd: float | None
    hold_h: float = STOCK_PERP_HOLD_H  # the hold the carry above was charged for


def reference(ctx: PerpCtx, quote: Quote | None, label: Label, now: datetime, max_age_s: int = REF_MAX_AGE_S) -> Reference:
    if label.ref_kind == "schwab" and quote and now - quote.quote_time <= timedelta(seconds=max_age_s):
        return Reference("schwab", quote.symbol, (quote.bid + quote.ask) / 2, quote.bid, quote.ask,
                         quote.bid_size, quote.ask_size, quote.quote_time)
    return Reference("oracle", ctx.symbol, ctx.oracle, None, None, None, None, now)


def basis_bps(mark: float, ref: float) -> float:
    return (mark / ref - 1) * 1e4


def direction(basis: float) -> Direction:
    return "short_perp_long_stock" if basis > 0 else "long_perp_short_stock"


def walk_perp(levels: list[PerpLevel], side: Literal["bid", "ask"], cap_usd: float | None) -> tuple[float, float, float]:
    """(contracts, usd, avg_price) down the ladder until cap_usd is spent; None = the whole displayed book."""
    contracts = usd = 0.0
    for lv in sorted((lv for lv in levels if lv.side == side), key=lambda lv: lv.level):
        take = lv.size if cap_usd is None else min(lv.size, (cap_usd - usd) / lv.price)
        if take <= 0:
            break
        contracts += take
        usd += take * lv.price
    return contracts, usd, usd / contracts if contracts else 0.0


def costs_bps(*, taker: float, spread_bps: float | None, funding_h: float, borrow_bps_yr: float, hold_h: float,
              side: Direction) -> dict:
    long_perp = side == "long_perp_short_stock"
    return {"perp_fee": 2e4 * taker, "stock_spread": spread_bps or 0.0,
            "funding": (1 if long_perp else -1) * funding_h * hold_h * 1e4,
            "borrow": borrow_bps_yr * hold_h / (24 * 365) if long_perp else 0.0}


def _net(side: Direction, basis: float, c: dict) -> float:
    return (basis if side == "short_perp_long_stock" else -basis) - sum(c.values())


def score(ctx: PerpCtx, book: list[PerpLevel], ref: Reference, *, hold_h: float = STOCK_PERP_HOLD_H,
          borrow_bps_yr: float = STOCK_PERP_BORROW_BPS_YR) -> StockPerpEdge:
    basis = basis_bps(ctx.mark, ref.price)
    side = direction(basis)
    spread = (ref.ask - ref.bid) / ref.price * 1e4 if ref.kind == "schwab" else None
    c = costs_bps(taker=HYPERLIQUID_TAKER["growth" if ctx.growth_mode else "standard"], spread_bps=spread,
                  funding_h=ctx.funding_h, borrow_bps_yr=borrow_bps_yr, hold_h=hold_h, side=side)
    if ref.kind != "schwab":
        cap = None  # an oracle price has no size: no capacity claim
        contracts = usd = 0.0
    else:
        cap = ref.ask_size * ref.ask if side == "short_perp_long_stock" else ref.bid_size * ref.bid
        contracts, usd, _ = walk_perp(book, "bid" if side == "short_perp_long_stock" else "ask", cap)
    return StockPerpEdge(ctx.symbol, side, ref.kind, ref.price, ctx.mark, basis, c["perp_fee"], spread,
                         c["funding"], c["borrow"], _net(side, basis, c), contracts, usd, cap, hold_h)


def gross_to_net(edge: StockPerpEdge, hold_h: float) -> float:
    """Net bps had the position been held hold_h: funding and borrow scale with the hold, the rest does not."""
    return edge.net_bps - (edge.funding_bps + edge.borrow_bps) * (hold_h / edge.hold_h - 1)


def edge_row(ts: datetime, e: StockPerpEdge, fee_version: str) -> dict:
    return {"ts": ts, "symbol": e.symbol, "direction": e.direction, "ref_kind": e.ref_kind, "reference": e.reference,
            "mark": e.mark, "basis_bps": e.basis_bps, "perp_fee_bps": e.perp_fee_bps,
            "stock_spread_bps": e.stock_spread_bps, "funding_bps": e.funding_bps, "borrow_bps": e.borrow_bps,
            "net_bps": e.net_bps, "capacity_contracts": e.capacity_contracts, "capacity_usd": e.capacity_usd,
            "fee_version": fee_version}


def episode_points(rows: list[dict]) -> list[tuple[datetime, Edge]]:
    return [(r["ts"], Edge(r["direction"], r["net_bps"] / 1e4, r["capacity_contracts"], r["capacity_usd"],
                           r["net_bps"] / 1e4)) for r in rows]


def annualize(edges: list[Edge], end: datetime) -> float:
    return median(e.avg_edge for e in edges) * 365 * 24 / STOCK_PERP_HOLD_H
