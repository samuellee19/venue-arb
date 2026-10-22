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

from venue_arb import config, taxonomy
from venue_arb.edges import Edge, score
from venue_arb.episodes import MIN_EDGE
from venue_arb.exact import ExactPair
from venue_arb.fees import US_EXECUTABLE, transfer_cost
from venue_arb.ladder_edges import _LEGS, admissible, score_spread, select_pairs
from venue_arb.models import Level

LIVE_MAX_AGE_S = 300  # a leg's latest snapshot older than this -> not live
PAIR_KEY_SEP = "~"  # key = f"{k_id}~{pm_id}~{direction}"
MIN_CAPACITY_USD = 100.0  # the episode rule (episodes.find's default): executable = >= 1¢ net and >= $100
SPARK_HOURS = 24
US = US_EXECUTABLE["kalshi"] and US_EXECUTABLE["polymarket"]
Snap = tuple[datetime, list[Level]]  # (snapshot ts, levels of one market at that ts)


class _M(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Leg(_M):
    venue: str
    market_id: str
    side: Literal["yes", "no"]
    title: str
    url: str
    settlement_source: str | None
    snapshot_at: datetime | None


class BookLevel(_M):
    price: float
    size: float


class Check(_M):
    name: Literal["same_instant", "exact_match", "basis_bound", "fees_depth", "jurisdiction"]
    status: Literal["ok", "fail", "partial"]
    detail: str


class Verdict(_M):
    label: str
    checks: list[Check]  # fixed order: same_instant|exact_match, basis_bound, fees_depth, jurisdiction


class Pair(_M):
    key: str
    kind: Literal["exact", "near_exact", "ladder"]
    category: str
    title: str
    settles_at: datetime
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
    method: dict[str, str]  # keys matching, edge, crypto, not_shown
    note_path: str  # "reports/research-note-v1.md"


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


def build_live(*, now: datetime, exact_rows: list[Pair], ladder_rows: list[Pair], basis: dict[str, Basis],
               episodes: list[Episode], annotations: list[Annotation], kpi_inputs: dict,
               attribution: list[str], caveats: list[str], collector_last: datetime | None,
               research: ResearchSummary | None = None) -> LiveBundle:
    """`episodes` = the last 7 days of scoped opportunities (already the >= 1¢, >= $100, >= 2 snapshots rule);
    kpi_inputs keys (each optional): checks_window, admissible_checks, violations, screened_share_ge_1c,
    screened_hours. pairs_live counts distinct market pairs (k_id, pm_id) per kind, not direction rows."""
    pairs = exact_rows + ladder_rows
    live = [p for p in pairs if p.live]
    # near-exact rows are basis exposure, never the headline edge
    top = max((p for p in live if p.net_edge_cents is not None and p.kind != "near_exact"),
              key=lambda p: p.net_edge_cents, default=None)
    kpis = Kpis(
        episodes_7d=len(episodes),
        best_edge=None if top is None else {"key": top.key, "net_edge_cents": top.net_edge_cents,
                                            "capacity_usd": top.capacity_usd},
        pairs_live={kind: len({(p.legs[0].market_id, p.legs[1].market_id) for p in live if p.kind == kind})
                    for kind in ("exact", "near_exact", "ladder")},
        **{f: kpi_inputs.get(f) for f in ("checks_window", "admissible_checks", "violations",
                                          "screened_share_ge_1c", "screened_hours")})
    return LiveBundle(computed_at=now, collector_last_snapshot_at=collector_last,
                      show_books=config.DASHBOARD_SHOW_BOOKS, basis=basis, kpis=kpis, pairs=pairs,
                      episodes=episodes, annotations=annotations, attribution=attribution, caveats=caveats,
                      categories=taxonomy.DISPLAY | {p.category: p.category for p in pairs
                                                     if p.category not in taxonomy.DISPLAY},
                      research=research)
