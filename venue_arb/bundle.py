"""live.json builder: latest books, exact pairs and crypto ladders -> the dashboard bundle (pure: no qa, no I/O).

The JSON schema of LiveBundle is the live.json contract. Books (price levels and sizes) appear only while
config.DASHBOARD_SHOW_BOOKS is True, read at call time (tests monkeypatch it); otherwise only derived numbers
(net edge, fees, capacity) leave this module. `titles` maps (venue, market_id) -> listing dict with "title",
"category", "resolution_source" and "raw" (listings.raw); `books` maps (venue, market_id) -> Snap.
"""
from collections.abc import Callable
from datetime import datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict

from venue_arb import config, sessions, stock_perps, taxonomy
from venue_arb.edges import Edge, score
from venue_arb.episodes import MIN_EDGE
from venue_arb.exact import ExactPair
from venue_arb.fees import HYPERLIQUID_TAKER, US_EXECUTABLE, transfer_cost
from venue_arb.ladder_edges import _LEGS, admissible, score_spread, select_pairs
from venue_arb.models import Level
from venue_arb.sources.hyperliquid import Label, PerpCtx, PerpLevel
from venue_arb.stock_perps import StockPerpEdge

LIVE_MAX_AGE_S = 300  # a leg's latest snapshot older than this -> not live
PAIR_KEY_SEP = "~"  # key = f"{k_id}~{pm_id}~{direction}"; a stock perp's: f"{perp}~{stock}~stock_perp" (stock_key)
MIN_CAPACITY_USD = 100.0  # the episode rule (episodes.find's default): executable = >= 1¢ net and >= $100
SPARK_HOURS = 24
US = US_EXECUTABLE["kalshi"] and US_EXECUTABLE["polymarket"]
Snap = tuple[datetime, list[Level]]  # (snapshot ts, levels of one market at that ts)
SnapPerp = tuple[datetime, list[PerpLevel]]  # (collector tick ts, book levels of one perp at that ts)


class _M(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Leg(_M):
    venue: str
    market_id: str
    side: Literal["yes", "no", "long", "short"]
    title: str
    url: str | None  # None: no page to link (the stock leg of a stock perp)
    settlement_source: str | None
    snapshot_at: datetime | None


class BookLevel(_M):
    price: float
    size: float


class Check(_M):
    name: Literal["same_instant", "exact_match", "basis_bound", "fees_depth", "jurisdiction", "reference", "fees_carry"]
    status: Literal["ok", "fail", "partial"]
    detail: str


class Verdict(_M):
    label: str
    checks: list[Check]  # fixed order: same_instant|exact_match, basis_bound, fees_depth, jurisdiction;
    # stock perps: reference, fees_carry, fees_depth, jurisdiction


class Pair(_M):
    key: str
    kind: Literal["exact", "near_exact", "ladder", "stock_perp"]
    category: str
    title: str
    settles_at: datetime | None  # None for a stock perp (perpetual)
    ladder: str | None  # "BTC~2026-10-20T16:00:00+00:00" for ladder/near_exact rows, else None
    legs: list[Leg]  # [kalshi, polymarket]
    direction: str
    live: bool
    net_edge_cents: float | None
    net_edge_bps: float | None
    net_edge_roi: float | None  # fraction: net_edge_cents / (100 - net_edge_cents); None with no edge
    fees_cents: float | None
    capacity_contracts: float
    capacity_usd: float
    books: list[list[BookLevel]] | None  # [k_asks, pm_asks] top BOOK_DEPTH; None when DASHBOARD_SHOW_BOOKS is False
    verdict: Verdict
    us_executable: bool
    fee_params: dict  # {"k": {"fee_type", "fee_multiplier"}, "pm": {"schedule", "fees_enabled"}} for /api/walk
    spark: list[float | None]  # 24 hourly points, oldest first; None = no scored row that hour
    # stock-perp rows only (net_edge_cents / net_edge_bps / net_edge_roi carry net_bps in the pair rows' units)
    basis_bps: float | None = None  # mark vs reference
    net_bps: float | None = None  # after perp fees, perp spread and funding over hold_hours
    funding_bps_h: float | None = None  # the venue's hourly funding rate
    next_funding_at: datetime | None = None
    reference: dict | None = None  # {"kind": "oracle", "state": "external" | "internal", "asof"}
    trade: str | None = None  # "short xyz:NVDA / long NVDA"
    hold_hours: int | None = None
    sensitivity: dict | None = None  # {"net_bps_1h", "net_bps_7d", "net_bps_with_borrow"}
    # the edge's cost lines over the hold, bps of notional (funding_bps is a cost: negative when received); None on a
    # stale row, stock_spread_bps None on an oracle row
    costs: dict | None = None  # {"perp_fee_bps", "perp_spread_bps", "funding_bps"}


class Basis(_M):
    up: float
    down: float
    window_days: int
    asof: datetime


class Episode(_M):
    key: str
    start: datetime
    end: datetime | None
    max_edge_cents: float
    median_capacity_usd: float
    transfer_cost_usd: float


class Annotation(_M):
    kind: Literal["settlement", "listing", "basis_change", "collector_gap", "threshold"]
    at: datetime
    until: datetime | None
    key: str | None
    label: str


class Kpis(_M):
    episodes_7d: int
    best_edge: dict | None  # {"key", "net_edge_cents", "capacity_usd"}
    pairs_live: dict  # {"exact", "near_exact", "ladder"}
    checks_window: list[datetime] | None
    admissible_checks: int | None
    violations: int | None
    screened_share_ge_1c: float | None
    screened_hours: int | None
    stock_perps: dict | None = None  # {"names_live", "episodes_7d"}; None while the stock-perp tier is off


class ResearchSeries(_M):
    ts: list[datetime]
    values: list[float | None]


class ResearchHist(_M):
    edges: list[float]  # ¢; len(edges) == len(counts) + 1
    counts: list[int]


class ResearchLadderSeries(_M):
    ts: list[datetime]
    checks: list[int]
    violations: list[int]


class StockPerpResearch(_M):
    names: int
    snapshots: int  # distinct (symbol, ts) scored
    window_start: datetime | None  # first scored tick
    episodes: int
    max_net_bps: float | None  # largest net basis after round-trip costs and the 24 h hold
    share_hours_abs_ge_10bps: float | None  # of external-session hours, the share with |basis| >= 10 bps
    hourly: ResearchSeries  # max net_bps per hour, whole history
    by_name: list[dict]  # {"symbol", "name", "max_net_bps", "hours_ge_10"}, top 10 by max_net_bps
    # episodes re-found over the trailing window_days at the 24 h hold (episodes_base), at 1 h, at 168 h and with borrow
    # (net - borrow_bps), so the four are comparable; `episodes` above is the stored all-history count
    # {"episodes_base", "episodes_1h", "episodes_7d", "episodes_with_borrow", "window_days"}
    sensitivity: dict
    weekends: dict  # weekends.summary plus "rows": the last 8 weekends of the top 5 names
    headline: str


class ResearchSummary(_M):
    computed_at: datetime
    window_start: datetime | None  # executable tier: first order-book snapshot in scope
    screened_start: datetime | None  # screened tier: first and last hourly row
    screened_end: datetime | None
    ladder_start: datetime | None  # ladder tier: first aligned snapshot
    headline: list[str]  # 2-3 sentences, numbers already formatted, publisher-owned
    pairs_eligible: int
    pairs_reviewed: int
    snapshots: int
    max_net_edge_cents: float | None
    episodes_pair: int
    episodes_ladder: int
    episodes_us: int
    episode_median_minutes: float | None
    episode_median_capacity_usd: float | None
    screened_rows: int
    screened_share_ge_1c: float | None
    screened_share_early: float | None
    ladder_checks: int | None
    ladder_violations: int
    ladder_scored: int
    basis: dict[str, Basis]
    basis_q: float  # the basis bound's quantile (basis.BASIS_Q); the client words it as a percentile
    review: dict | None  # {"precision": float, "labels": int, "asof": datetime} or None
    exec_hourly: ResearchSeries  # max net edge (¢) per hour over all scoped pairs, whole history
    screened_hist: ResearchHist  # net-gap histogram, 1¢ bins, 23 edges -11..11 (end bins open: < -10¢, >= +10¢)
    ladder_hourly: ResearchLadderSeries  # admissible checks and violations per counted hour
    method: dict[str, str]  # keys matching, edge, crypto, not_shown, stock_perps
    note_path: str  # "reports/research-note-v1.md"
    stock_perps: StockPerpResearch | None = None  # None while the stock-perp tier is off


class LiveBundle(_M):
    computed_at: datetime
    collector_last_snapshot_at: datetime | None
    show_books: bool
    basis: dict[str, Basis]
    kpis: Kpis
    pairs: list[Pair]
    episodes: list[Episode]
    annotations: list[Annotation]
    attribution: list[str]
    caveats: list[str]
    categories: dict[str, str] = {}  # category slug -> display name
    research: ResearchSummary | None = None


def roi(net_edge_cents: float | None) -> float | None:
    """Net edge per dollar laid out: a pair pays $1 for an outlay of 1 - net, so 0.5¢ net -> 0.005 / 0.995."""
    return None if net_edge_cents is None else net_edge_cents / (100 - net_edge_cents)


def pair_key(k_id: str, pm_id: str, direction: str) -> str:
    return PAIR_KEY_SEP.join((k_id, pm_id, direction))


def stock_key(perp: str, stock: str) -> str:
    """A stock-perp row's key: the direction is a field, not identity, so a basis sign flip keeps the row's history."""
    return pair_key(perp, stock, "stock_perp")


def market_url(venue: str, listing: dict) -> str:
    ev = listing["raw"]["event"]
    if venue == "kalshi":
        # observed 2026-10-20 (docs/venues.md): /markets/{series}/{slug}/{event}, lower case; any slug resolves
        return f"https://kalshi.com/markets/{ev['series_ticker'].lower()}/market/{ev['event_ticker'].lower()}"
    return f"https://polymarket.com/event/{ev['slug']}"


def _half_up(x: float, q: str) -> Decimal:
    """x at the 4 dp it is published with, rounded half-up to q ("0.1", "1"), as the client's toLocaleString shows it
    (f"{5.35:.1f}" is "5.3": Python rounds the binary float, which sits just below 5.35)."""
    return Decimal(str(round(x, 4))).quantize(Decimal(q), ROUND_HALF_UP)


def verdict(kind: str, direction: str, edge: Edge | None, *, a: float | None, b: float | None,
            up: float | None, down: float | None, us_executable: bool, both_live: bool,
            first: tuple[float, float] | None = None) -> Verdict:
    """`edge` None = not scored (a leg is stale); edge.best_edge None = no asks on one side. `first` = (contracts,
    fee ¢ per contract) of the walk's first slice, for the fees_depth detail; the contract count (a top-of-book
    size) appears only while config.DASHBOARD_SHOW_BOOKS is True."""
    best = edge.best_edge if edge else None
    executable = best is not None and best >= MIN_EDGE and edge.capacity_usd >= MIN_CAPACITY_USD
    if kind == "exact":
        checks = [Check(name="exact_match", status="ok", detail="same question on both venues"),
                  Check(name="basis_bound", status="ok", detail="exact match: same settlement source")]
    else:
        need, gap, diff = (down, b - a, "PM − K") if direction == "k_yes_pm_no" else (up, a - b, "K − PM")
        checks = [Check(name="same_instant", status="ok", detail="both legs settle at the same instant"),
                  Check(name="basis_bound", status="ok" if direction in admissible(a, b, up, down) else "fail",
                        detail=f"needs {diff} ≥ ${need:,.0f} (99.9th pct, {config.BASIS_WINDOW_DAYS} d); "
                               f"gap ${round(gap):,}")]
    if best is None:
        checks.append(Check(name="fees_depth", status="fail",
                            detail="not walked: " + ("no asks on one side" if edge else "a leg is stale")))
    else:
        detail = f"net {_half_up(100 * best, '0.1'):+}¢"
        if first is None:
            detail += " on the first slice"
        elif config.DASHBOARD_SHOW_BOOKS:
            detail += f" at the first {first[0]:,.0f} contracts; fees {first[1]:.1f}¢"
        else:
            detail += f" on the first slice; fees {first[1]:.1f}¢"
        checks.append(Check(name="fees_depth", status="ok" if executable else "partial" if best >= MIN_EDGE else "fail",
                            detail=detail))
    us = "both legs open" if us_executable else "Polymarket rejects US orders"
    checks.append(Check(name="jurisdiction", status="ok" if us_executable else "partial",
                        detail=f"anywhere: both legs open · US: {us}"))
    if edge is not None and best is None:
        label = "No asks on one side"
    elif not both_live:
        label = "Stale · a leg has no snapshot in 5 min"
    elif kind == "near_exact":
        label = "Not an arbitrage · basis exposure"
    elif executable:
        cap = edge.capacity_usd
        label = (f"Executable edge · +{_half_up(100 * best, '0.1')}¢ · ${_half_up(cap, '1'):,} before transfer · "
                 f"${_half_up(cap - transfer_cost(cap), '1'):,} after")
    else:
        label = "Exact match · no edge" if kind == "exact" else "Spread · within basis bound · no edge"
    return Verdict(label=label, checks=checks)


def _live(k_book: Snap | None, pm_book: Snap | None, settles_at: datetime, now: datetime) -> bool:
    fresh = timedelta(seconds=LIVE_MAX_AGE_S)
    return bool(k_book and pm_book and now - k_book[0] <= fresh and now - pm_book[0] <= fresh and now < settles_at)


def _asks(book: Snap | None, side: str) -> list[Level]:
    return [lv for lv in book[1] if lv.side == side] if book else []


def _leg(venue: str, market_id: str, side: str, listing: dict, book: Snap | None) -> Leg:
    return Leg(venue=venue, market_id=market_id, side=side, title=listing["title"], url=market_url(venue, listing),
               settlement_source=listing.get("resolution_source"), snapshot_at=book[0] if book else None)


def _pair(kind, direction, k_id, pm_id, k_side, pm_side, titles, k_book, pm_book, edge, live, fee_params, sparks, *,
          category, title, settles_at, ladder=None, a=None, b=None, up=None, down=None) -> Pair:
    ka, pa = _asks(k_book, k_side), _asks(pm_book, pm_side)
    best = edge.best_edge if edge else None
    net = bps = fee = first = None
    if best is not None:
        # first slice: best_edge = 1 - k0 - pm0 - fees/q, so the fees per contract follow from it
        fee = 100 * (1 - ka[0].price - pa[0].price - best)
        first = (min(ka[0].size, pa[0].size), fee)
        net, bps, fee = round(100 * best, 4), round(best / (1 - best) * 1e4, 4), round(fee, 4)
    key = pair_key(k_id, pm_id, direction)
    books = None
    if config.DASHBOARD_SHOW_BOOKS:
        books = [[BookLevel(price=lv.price, size=lv.size) for lv in xs[:config.BOOK_DEPTH]] for xs in (ka, pa)]
    return Pair(
        key=key, kind=kind, category=category, title=title, settles_at=settles_at, ladder=ladder,
        legs=[_leg("kalshi", k_id, k_side, titles[("kalshi", k_id)], k_book),
              _leg("polymarket", pm_id, pm_side, titles[("polymarket", pm_id)], pm_book)],
        direction=direction, live=live, net_edge_cents=net, net_edge_bps=bps, net_edge_roi=roi(net), fees_cents=fee,
        capacity_contracts=edge.capacity_contracts if edge else 0.0, capacity_usd=edge.capacity_usd if edge else 0.0,
        books=books,
        verdict=verdict(kind, direction, edge, a=a, b=b, up=up, down=down, us_executable=US, both_live=live,
                        first=first),
        us_executable=US, fee_params=fee_params, spark=sparks.get(key, [None] * SPARK_HOURS))


def _fee_params(series_fee: dict, schedule: dict | None, enabled: bool) -> dict:
    return {"k": {"fee_type": series_fee["fee_type"], "fee_multiplier": series_fee["fee_multiplier"]},
            "pm": {"schedule": schedule, "fees_enabled": enabled}}


def exact_pair_rows(pair: ExactPair, titles: dict, k_book: Snap | None, pm_book: Snap | None,
                    series_fee: dict, sparks: dict, now: datetime) -> list[Pair]:
    """Both directions of one exact pair; scored only while live."""
    live = _live(k_book, pm_book, pair.close_time, now)
    edges = {e.direction: e for e in score(pair, k_book[1], pm_book[1], series_fee)} if live else {}
    flip = {"yes": "no", "no": "yes"}
    sides = {"k_yes_pm_no": ("yes", flip[pair.yes_maps_to]), "k_no_pm_yes": ("no", pair.yes_maps_to)}  # as score()
    fp = _fee_params(series_fee, pair.pm_fee_schedule, pair.pm_fees_enabled)
    k = titles[("kalshi", pair.kalshi_id)]
    return [_pair("exact", d, pair.kalshi_id, pair.pm_id, *sides[d], titles, k_book, pm_book, edges.get(d), live, fp,
                  sparks, category=k["category"], title=k["title"], settles_at=pair.close_time) for d in sides]


def ladder_pair_rows(asset: str, instant: datetime, ladder: dict, books: dict, up: float, down: float,
                     k_fee: Callable[[str], dict], pm_fee: Callable[[str], tuple[dict | None, bool]], titles: dict,
                     sparks: dict, now: datetime) -> list[Pair]:
    """One row per (booked Kalshi strike, direction): the best-edge admissible PM strike of select_pairs' plan (ties
    and unscored: the nearest); near-exact strikes as separate "near_exact" rows. Strikes without a book are skipped."""
    tag = f"{asset}{PAIR_KEY_SEP}{instant.isoformat()}"
    ps = [p for p in ladder["polymarket"] if ("polymarket", p.market_id) in books]
    rows: list[Pair] = []
    for k in ladder["kalshi"]:
        if (k_book := books.get(("kalshi", k.market_id))) is None:
            continue
        sf = k_fee(k.market_id)

        def row(kind, p, d, edge, live, k=k, k_book=k_book, sf=sf):
            pm_book = books[("polymarket", p.market_id)]
            return _pair(kind, d, k.market_id, p.market_id, *_LEGS[d], titles, k_book, pm_book, edge, live,
                         _fee_params(sf, *pm_fee(p.market_id)), sparks, category="crypto_price",
                         title=f"{asset} above ${k.level:,.2f} (Kalshi) / ${p.level:,.2f} (Polymarket)",
                         settles_at=instant, ladder=tag, a=k.level, b=p.level, up=up, down=down)

        plan, _ = select_pairs(k.level, ps, up, down)
        best: dict[str, tuple] = {}  # direction -> (p, edge, live), the best arb candidate so far
        for p, kind, dirs in plan:
            pm_book = books[("polymarket", p.market_id)]
            live = _live(k_book, pm_book, instant, now)
            for d in dirs:
                edge = score_spread(d, k_book[1], pm_book[1], sf, *pm_fee(p.market_id)) if live else None
                if kind == "near_exact":
                    rows.append(row("near_exact", p, d, edge, live))
                elif d not in best or _rank(edge) > _rank(best[d][1]):
                    best[d] = (p, edge, live)
        rows += [row("ladder", p, d, edge, live) for d, (p, edge, live) in best.items()]
    return rows


def _rank(edge: Edge | None) -> float:
    return edge.best_edge if edge and edge.best_edge is not None else float("-inf")


def stock_perp_verdict(edge: StockPerpEdge, state: str, live: bool, now: datetime, *, has_book: bool = True,
                       asof: datetime | None = None) -> Verdict:
    """The public stock-perp verdict: checks reference, fees_carry, fees_depth, jurisdiction. The basis is the perp
    mark against the venue oracle; `now` is the publisher's tick and `asof` the snapshot's (the oracle's age).
    `state` internal (the oracle follows the perp) is never an edge. `has_book` False (the perp book fetch failed):
    "No asks on one side", as the pair tiers word a missing side."""
    internal = state == "internal"
    nobook = live and not has_book
    ok = live and edge.net_bps >= config.STOCK_PERP_MIN_NET_BPS and edge.capacity_usd >= MIN_CAPACITY_USD
    age = max(0.0, (now - (asof or now)).total_seconds())
    checks = [Check(name="reference", status="partial" if internal else "ok",
                    detail="oracle internal: market closed" if internal else f"oracle live, {age:.0f} s old")]
    if live:
        net = edge.net_bps
        checks.append(Check(name="fees_carry", status="ok" if net >= config.STOCK_PERP_MIN_NET_BPS else "fail",
                            detail=f"net {net:+.1f} bps after 2× taker, perp spread and {edge.hold_h:g} h funding"))
        cap = edge.capacity_usd
        checks.append(Check(name="fees_depth", status="ok" if cap >= MIN_CAPACITY_USD else "fail",
                            detail="not walked: no perp book" if nobook
                            else f"${cap:,.0f} perp depth vs ${MIN_CAPACITY_USD:,.0f} minimum"))
    else:
        checks += [Check(name="fees_carry", status="fail", detail="not scored: a leg is stale"),
                   Check(name="fees_depth", status="fail", detail="not walked: a leg is stale")]
    checks.append(Check(name="jurisdiction", status="partial",
                        detail="anywhere: perp leg open · US: Hyperliquid blocks US traders"))
    if not live:
        label = "Stale · a leg has no snapshot in 5 min"
    elif internal:
        label = "Oracle internal · market closed"
    elif nobook:
        label = "No asks on one side"
    elif ok:
        label = f"Oracle basis · {edge.net_bps:+.0f} bps before stock costs · ${edge.capacity_usd:,.0f} perp depth"
    else:
        label = "Oracle basis · within perp costs"
    return Verdict(label=label, checks=checks)


def stock_perp_rows(perps: list[tuple[PerpCtx, Label, SnapPerp]], sparks: dict, now: datetime) -> list[Pair]:
    """One row per perp name, scored in the oracle mode (Hyperliquid data only). net_edge_cents is net_bps / 100
    (bps of notional read as ¢ per $100), so the Explorer's ¢ axis and sparkline share a unit with the pair rows. Past
    LIVE_MAX_AGE_S the row is unscored (Stale, nets None). The oracle is aged from the snapshot's tick `ts`, not
    `now` (the collector inserts every few ticks, so a stored tick is minutes old)."""
    rows = []
    for ctx, label, (ts, levels) in perps:
        state = sessions.state(label, ts)
        edge = stock_perps.score(ctx, levels, stock_perps.reference(ctx, None, label, ts), mode="oracle", session=state)
        live = now - ts <= timedelta(seconds=LIVE_MAX_AGE_S)
        sym, stock = ctx.symbol, label.ref_symbol or ctx.symbol
        short_perp = edge.direction == "short_perp_long_stock"
        perp_side, stock_side = ("short", "long") if short_perp else ("long", "short")
        key = stock_key(sym, stock)
        net = round(edge.net_bps, 4) if live else None
        taker = HYPERLIQUID_TAKER["growth" if ctx.growth_mode else "standard"]
        hold = edge.hold_h
        rows.append(Pair(
            key=key, kind="stock_perp", category="stock_perps",
            title=f"{label.name} ({label.exchange})" if label.exchange else label.name, settles_at=None, ladder=None,
            legs=[Leg(venue="hyperliquid", market_id=sym, side=perp_side, title=sym,
                      url=f"https://app.hyperliquid.xyz/trade/{sym}", settlement_source=None, snapshot_at=ts),
                  Leg(venue=label.exchange.lower() or "stock", market_id=stock, side=stock_side, title=label.name,
                      url=None, settlement_source=None, snapshot_at=None)],
            direction=edge.direction, live=live,
            net_edge_cents=None if net is None else net / 100, net_edge_bps=net,
            net_edge_roi=None if net is None else net / 1e4,
            fees_cents=None if net is None else round(edge.perp_fee_bps / 100, 4),
            capacity_contracts=edge.capacity_contracts if live else 0.0,
            capacity_usd=edge.capacity_usd if live else 0.0, books=None,
            verdict=stock_perp_verdict(edge, state, live, now, has_book=bool(levels), asof=ts), us_executable=False,
            fee_params={"perp": {"taker": taker, "growth_mode": ctx.growth_mode},
                        "stock": {"borrow_bps_yr": config.STOCK_PERP_BORROW_BPS_YR}},
            spark=sparks.get(key, [None] * SPARK_HOURS),
            basis_bps=round(edge.basis_bps, 4) if live else None, net_bps=net, funding_bps_h=ctx.funding_h * 1e4,
            next_funding_at=now.replace(minute=0, second=0, microsecond=0) + timedelta(hours=1),
            reference={"kind": "oracle", "state": state, "asof": ts.isoformat()},
            trade=f"{perp_side} {sym} / {stock_side} {stock}", hold_hours=int(hold),
            costs=None if not live else {
                "perp_fee_bps": round(edge.perp_fee_bps, 4), "perp_spread_bps": round(edge.perp_spread_bps, 4),
                "funding_bps": round(edge.funding_bps, 4)},
            sensitivity=None if not live else {
                "net_bps_1h": round(stock_perps.gross_to_net(edge, 1), 4),
                "net_bps_7d": round(stock_perps.gross_to_net(edge, 168), 4),
                "net_bps_with_borrow": round(edge.net_bps - edge.borrow_bps, 4)}))
    return rows


def build_live(*, now: datetime, exact_rows: list[Pair], ladder_rows: list[Pair], basis: dict[str, Basis],
               episodes: list[Episode], annotations: list[Annotation], kpi_inputs: dict,
               attribution: list[str], caveats: list[str], collector_last: datetime | None,
               research: ResearchSummary | None = None, stock_rows: list[Pair] | None = None) -> LiveBundle:
    """`episodes` = the last 7 days of scoped opportunities (already the >= 1¢, >= $100, >= 2 snapshots rule);
    kpi_inputs keys (each optional): checks_window, admissible_checks, violations, screened_share_ge_1c,
    screened_hours, stock_perps ({"episodes_7d"}). pairs_live counts distinct market pairs (k_id, pm_id) per kind, not
    direction rows. `stock_rows` None = the stock-perp tier is off; stock rows never feed best_edge (bps, not pair ¢)."""
    pairs = exact_rows + ladder_rows + (stock_rows or [])
    live = [p for p in pairs if p.live]
    # near-exact rows are basis exposure, never the headline edge; stock perps are another tier
    top = max((p for p in live if p.net_edge_cents is not None and p.kind not in ("near_exact", "stock_perp")),
              key=lambda p: p.net_edge_cents, default=None)
    kpis = Kpis(
        episodes_7d=len(episodes),
        best_edge=None if top is None else {"key": top.key, "net_edge_cents": top.net_edge_cents,
                                            "capacity_usd": top.capacity_usd},
        pairs_live={kind: len({(p.legs[0].market_id, p.legs[1].market_id) for p in live if p.kind == kind})
                    for kind in ("exact", "near_exact", "ladder")},
        **{f: kpi_inputs.get(f) for f in ("checks_window", "admissible_checks", "violations",
                                          "screened_share_ge_1c", "screened_hours")},
        stock_perps=None if stock_rows is None else {
            "names_live": sum(p.live for p in stock_rows),
            "episodes_7d": kpi_inputs.get("stock_perps", {}).get("episodes_7d", 0)})
    return LiveBundle(computed_at=now, collector_last_snapshot_at=collector_last,
                      show_books=config.DASHBOARD_SHOW_BOOKS, basis=basis, kpis=kpis, pairs=pairs,
                      episodes=episodes, annotations=annotations, attribution=attribution, caveats=caveats,
                      categories=taxonomy.DISPLAY | {p.category: p.category for p in pairs
                                                     if p.category not in taxonomy.DISPLAY},
                      research=research)
