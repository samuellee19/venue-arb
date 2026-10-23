"""Stock-perp basis against the stock, round-trip costs over a stated hold, and the capacity the stock side allows (pure)."""
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta
from statistics import median
from typing import Literal

from venue_arb.config import (
    REF_MAX_AGE_S,
    STOCK_PERP_BORROW_BPS_YR,
    STOCK_PERP_HOLD_H,
    STOCK_PERP_MIN_NET_BPS,
)
from venue_arb.edges import Edge
from venue_arb.fees import HYPERLIQUID_TAKER
from venue_arb.sources.hyperliquid import Label, PerpCtx, PerpLevel
from venue_arb.sources.schwab import Quote

Direction = Literal["short_perp_long_stock", "long_perp_short_stock"]
Mode = Literal["executable", "oracle"]


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
    perp_spread_bps: float  # exit half-spread of the perp's top of book
    stock_spread_bps: float | None
    funding_bps: float  # signed cost: negative when the position receives funding
    borrow_bps: float
    net_bps: float
    capacity_contracts: float
    capacity_usd: float
    stock_cap_usd: float | None
    hold_h: float = STOCK_PERP_HOLD_H  # the hold the carry above was charged for
    mode: Mode = "executable"
    session: Literal["external", "internal"] = "external"


def reference(ctx: PerpCtx, quote: Quote | None, label: Label, now: datetime, max_age_s: int = REF_MAX_AGE_S) -> Reference:
    # a one-sided (bid 0) or crossed quote is not a price: fall back to the oracle
    if (label.ref_kind == "schwab" and quote and 0 < quote.bid <= quote.ask
            and now - quote.quote_time <= timedelta(seconds=max_age_s)):
        return Reference("schwab", quote.symbol, (quote.bid + quote.ask) / 2, quote.bid, quote.ask,
                         quote.bid_size, quote.ask_size, quote.quote_time)
    return Reference("oracle", ctx.symbol, ctx.oracle, None, None, None, None, now)


def basis_bps(mark: float, ref: float) -> float:
    return (mark / ref - 1) * 1e4


def direction(basis: float) -> Direction:
    return "short_perp_long_stock" if basis > 0 else "long_perp_short_stock"


def walk_perp(levels: list[PerpLevel], side: Literal["bid", "ask"], cap_usd: float | None,
              accept: Callable[[float], bool] | None = None) -> tuple[float, float, float]:
    """(contracts, usd, avg_price) down the ladder until cap_usd is spent; None = the whole displayed book.
    `accept(price)` is asked of each level; the walk stops at the first level it refuses."""
    contracts = usd = 0.0
    for lv in sorted((lv for lv in levels if lv.side == side), key=lambda lv: lv.level):
        if cap_usd is not None and usd >= cap_usd:
            break  # checked before `take`, so float residue cannot buy a dust slice of the next level
        if accept and not accept(lv.price):
            break
        take = lv.size if cap_usd is None else min(lv.size, (cap_usd - usd) / lv.price)
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


def score(ctx: PerpCtx, book: list[PerpLevel], ref: Reference, *, mode: Mode = "executable",
          session: Literal["external", "internal"] = "external", hold_h: float = STOCK_PERP_HOLD_H,
          borrow_bps_yr: float = STOCK_PERP_BORROW_BPS_YR, min_net_bps: float = STOCK_PERP_MIN_NET_BPS) -> StockPerpEdge:
    """Capacity is the dollars fillable while each level's own net stays at or above `min_net_bps` (the rule the pair
    tier's edges.walk applies at 1 cent), capped by the stock leg's size; with no such level it is 0 and net is at the
    best price on the side. Net is taken from the walked average fill, not the mark: entry spread and depth are in it,
    the exit half-spread is a cost line. `basis_bps` stays mark vs reference as the displayed basis.
    `oracle` mode is the Hyperliquid-only view: the reference is the venue oracle, there is no stock leg (no cap, no
    stock spread), capacity is the perp depth that clears the floor, and borrow is reported but not in net_bps."""
    oracle = mode == "oracle"
    if oracle:
        ref = Reference("oracle", ctx.symbol, ctx.oracle, None, None, None, None, ref.asof)
    if ref.price <= 0:  # a zero reference is absent: use the oracle
        ref = Reference("oracle", ctx.symbol, ctx.oracle, None, None, None, None, ref.asof)
    if ref.price <= 0:
        return StockPerpEdge(ctx.symbol, "long_perp_short_stock", ref.kind, ref.price, ctx.mark, 0.0, 0.0, 0.0, None,
                             0.0, 0.0, 0.0, 0.0, 0.0, None, hold_h, mode, session)
    basis = basis_bps(ctx.mark, ref.price)
    side = direction(basis)
    short_perp = side == "short_perp_long_stock"
    spread = (ref.ask - ref.bid) / ref.price * 1e4 if ref.kind == "schwab" else None
    c = costs_bps(taker=HYPERLIQUID_TAKER["growth" if ctx.growth_mode else "standard"], spread_bps=spread,
                  funding_h=ctx.funding_h, borrow_bps_yr=borrow_bps_yr, hold_h=hold_h, side=side)
    walked = "bid" if short_perp else "ask"
    bids = sorted((lv for lv in book if lv.side == "bid"), key=lambda lv: lv.level)
    asks = sorted((lv for lv in book if lv.side == "ask"), key=lambda lv: lv.level)
    perp_spread = (asks[0].price - bids[0].price) / (asks[0].price + bids[0].price) * 1e4 if bids and asks else 0.0
    sign = 1 if short_perp else -1
    if oracle:
        c["borrow_assumed"], c["borrow"] = c["borrow"], 0.0  # shown, not charged
    fixed = sum(v for k, v in c.items() if k != "borrow_assumed") + perp_spread  # per-contract costs in bps; a level's net is its price edge less this
    cap = None
    if ref.kind != "schwab" and not oracle:
        contracts = usd = 0.0  # an oracle price has no size: no capacity claim
        fill = None
    else:
        if not oracle:
            cap = ref.ask_size * ref.ask if short_perp else ref.bid_size * ref.bid
        contracts, usd, fill = walk_perp(book, walked, cap,
                                         lambda px: sign * basis_bps(px, ref.price) - fixed >= min_net_bps)
    if not contracts:  # nothing walked: price the entry at the best level on that side, else the mark
        top = bids if short_perp else asks
        fill = top[0].price if top else ctx.mark
    fill_bps = sign * basis_bps(fill, ref.price)
    net = fill_bps - c["perp_fee"] - perp_spread - c["stock_spread"] - c["funding"] - c["borrow"]
    return StockPerpEdge(ctx.symbol, side, ref.kind, ref.price, ctx.mark, basis, c["perp_fee"], perp_spread, spread,
                         c["funding"], c.get("borrow_assumed", c["borrow"]), net, contracts, usd, cap, hold_h, mode, session)


def gross_to_net(edge: StockPerpEdge, hold_h: float) -> float:
    """Net bps had the position been held hold_h: funding and borrow scale with the hold; the fill, the perp fee,
    the perp exit spread and the stock spread (all inside net_bps) do not. Borrow is excluded in oracle mode."""
    if edge.hold_h <= 0:
        raise ValueError("edge.hold_h must be positive")
    borrow = 0.0 if edge.mode == "oracle" else edge.borrow_bps  # borrow is not in an oracle-mode net
    return edge.net_bps - (edge.funding_bps + borrow) * (hold_h / edge.hold_h - 1)


def edge_row(ts: datetime, e: StockPerpEdge, fee_version: str) -> dict:
    return {"ts": ts, "symbol": e.symbol, "direction": e.direction, "ref_kind": e.ref_kind, "reference": e.reference,
            "mark": e.mark, "basis_bps": e.basis_bps, "perp_fee_bps": e.perp_fee_bps,
            "perp_spread_bps": e.perp_spread_bps,
            "stock_spread_bps": e.stock_spread_bps, "funding_bps": e.funding_bps, "borrow_bps": e.borrow_bps,
            "net_bps": e.net_bps, "capacity_contracts": e.capacity_contracts, "capacity_usd": e.capacity_usd,
            "fee_version": fee_version, "mode": e.mode, "session": e.session}


def episode_points(rows: list[dict]) -> list[tuple[datetime, Edge]]:
    return [(r["ts"], Edge(r["direction"], r["net_bps"] / 1e4, r["capacity_contracts"], r["capacity_usd"],
                           r["net_bps"] / 1e4)) for r in rows]


def annualize(edges: list[Edge], end: datetime) -> float:
    return median(e.avg_edge for e in edges) * 365 * 24 / STOCK_PERP_HOLD_H
