"""note.summarize output -> the bundle's ResearchSummary (pure: no qa, no I/O)."""
from datetime import datetime

import numpy as np

from venue_arb import weekends
from venue_arb.basis import BASIS_Q
from venue_arb.bundle import (
    Basis,
    ResearchHist,
    ResearchLadderSeries,
    ResearchSeries,
    ResearchSummary,
    StockPerpResearch,
)
from venue_arb.config import BASIS_WINDOW_DAYS
from venue_arb.report import copy

NOTE_PATH = "reports/research-note-v1.md"
HIST_EDGES = list(range(-11, 12))  # ¢; [k, k+1) bins, the two end bins pool everything below -10¢ / from +10¢ up


def _c(x: float | None) -> float | None:
    return None if x is None else round(100 * x, 4)


def _exec_hourly(series: dict) -> ResearchSeries:
    best: dict = {}
    for pts in series.values():
        for ts, e in pts:
            h = ts.replace(minute=0, second=0, microsecond=0)
            best[h] = max(best.get(h, e), e)
    ts = sorted(best)
    return ResearchSeries(ts=ts, values=[_c(best[t]) for t in ts])


def _screened_hist(values: list[float]) -> ResearchHist:
    # round first so a computed 0.01 that lands at 0.99999...¢ still bins at 1¢
    c = np.clip(np.round(np.asarray(values, dtype=float) * 100, 6), -10.5, 10.5)
    counts, _ = np.histogram(c, bins=HIST_EDGES)
    return ResearchHist(edges=HIST_EDGES, counts=counts.tolist())


def _n(x, noun: str) -> str:
    n = x or 0
    return f"{n:,} {noun}" + ("" if n == 1 else "s")


def from_summary(s: dict, *, review: dict | None, computed_at: datetime) -> ResearchSummary:
    sc, ex, lad = s["screened"], s["executable"], s["ladder"]
    hourly = lad["hourly"]
    since = ex["start"].strftime("%d %b").lstrip("0") if ex["start"] else "—"
    max_edge = f"{100 * ex['best_edge_max']:.1f}¢" if ex["best_edge_max"] is not None else "—"
    headline = [
        copy.HEADLINE["question"],
        # pairs with snapshots in scope, the pairs the snapshots and episodes come from (not every eligible pair)
        copy.HEADLINE["answer"].format(pairs=_n(ex["pairs"], "exact pair"),
                                       snapshots=_n(ex["snapshots"], "order-book snapshot"), since=since,
                                       max_edge=max_edge, episodes=_n(ex["episodes"], "episode"),
                                       us_episodes=f"{s['jurisdiction']['us_episodes']:,}"),
        (copy.HEADLINE["ladders"].format(violations=f"{lad['pos_counted']:,}", checks=f"{lad['admissible_checks']:,}")
         if lad["admissible_checks"] is not None else copy.HEADLINE["ladders_none"])
        + (copy.HEADLINE["ladder_episodes"].format(episodes=_n(lad["episodes"], "episode")) if lad["episodes"] else ""),
    ]
    return ResearchSummary(
        computed_at=computed_at,
        window_start=ex["start"],
        screened_start=sc["start"],
        screened_end=sc["end"],
        ladder_start=lad["start"],
        headline=headline,
        pairs_eligible=s["pairs"]["eligible"],
        pairs_reviewed=s["pairs"]["reviewed"],
        snapshots=ex["snapshots"],
        max_net_edge_cents=_c(ex["best_edge_max"]),
        episodes_pair=ex["episodes"],
        episodes_ladder=lad["episodes"],
        episodes_us=s["jurisdiction"]["us_episodes"],
        episode_median_minutes=ex["duration_min_median"],
        episode_median_capacity_usd=ex["capacity_usd_median"],
        screened_rows=sc["rows"],
        screened_share_ge_1c=sc["share_ge_001"] if sc["rows"] else None,
        screened_share_early=sc["n_ge_001_early"] / sc["n_ge_001"] if sc["n_ge_001"] else None,
        ladder_checks=lad["admissible_checks"],
        ladder_violations=lad["pos_counted"],
        ladder_scored=lad["arb_checks_scored"],
        # an asset below LADDER_MIN_BASIS hours has no bound (its ladders are not scored), so it is left out
        basis={a: Basis(up=v["up"], down=v["down"], window_days=BASIS_WINDOW_DAYS, asof=computed_at)
               for a, v in lad["basis"].items() if v["up"] is not None},
        basis_q=BASIS_Q,
        review=review,
        exec_hourly=_exec_hourly(ex["series"]),
        screened_hist=_screened_hist(sc["values"]),
        ladder_hourly=ResearchLadderSeries(ts=[h["hour"] for h in hourly], checks=[h["checks"] for h in hourly],
                                           violations=[h["violations"] for h in hourly]),
        method=copy.METHOD,
        note_path=NOTE_PATH,
    )


def stock_perp_block(inputs: dict, computed_at: datetime) -> StockPerpResearch:
    """The publisher's stock-perp aggregates (publish.stock_perp_inputs) -> the Research tab's stock-perp block."""
    hourly = sorted(inputs["hourly"])
    best = max((v for _, v in hourly), default=None)
    by_name = sorted(inputs["by_name"], key=lambda r: (-r["max_net_bps"], r["symbol"]))[:10]
    top5 = [r["symbol"] for r in by_name[:5]]
    rows = []
    for sym in top5:  # the last 8 weekends of each, oldest first
        rows += sorted((r for r in inputs["weekend_rows"] if r["symbol"] == sym), key=lambda r: r["friday"])[-8:]
    start = inputs["window_start"]
    headline = copy.HEADLINE["stock_perps"].format(
        names=_n(inputs["names"], "stock perp"), snapshots=_n(inputs["snapshots"], "snapshot"),
        since=start.strftime("%d %b").lstrip("0") if start else "—",
        max_net=f"{best:.1f} bps" if best is not None else "—", episodes=_n(inputs["episodes"], "episode"))
    return StockPerpResearch(
        names=inputs["names"], snapshots=inputs["snapshots"], window_start=start, episodes=inputs["episodes"],
        max_net_bps=None if best is None else round(best, 2), share_hours_abs_ge_10bps=inputs["share_hours"],
        hourly=ResearchSeries(ts=[t for t, _ in hourly], values=[round(v, 2) for _, v in hourly]),
        by_name=by_name, sensitivity=inputs["sensitivity"],
        weekends=weekends.summary(inputs["weekend_rows"]) | {"rows": rows}, headline=headline)
