"""note.summarize output -> the bundle's ResearchSummary (pure: no qa, no I/O)."""
from datetime import datetime

import numpy as np

from venue_arb.bundle import Basis, ResearchHist, ResearchLadderSeries, ResearchSeries, ResearchSummary
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


def _n(x) -> str:
    return f"{x or 0:,}"


def from_summary(s: dict, *, review: dict | None, computed_at: datetime) -> ResearchSummary:
    sc, ex, lad = s["screened"], s["executable"], s["ladder"]
    hourly = lad["hourly"]
    since = ex["start"].strftime("%d %b").lstrip("0") if ex["start"] else "—"
    max_edge = f"{100 * ex['best_edge_max']:.1f}¢" if ex["best_edge_max"] is not None else "—"
    headline = [
        copy.HEADLINE["question"],
        copy.HEADLINE["answer"].format(pairs=_n(s["pairs"]["eligible"]), snapshots=_n(ex["snapshots"]), since=since,
                                       max_edge=max_edge, episodes=_n(ex["episodes"]),
                                       us_episodes=_n(s["jurisdiction"]["us_episodes"])),
        copy.HEADLINE["ladders"].format(violations=_n(lad["pos_counted"]), checks=_n(lad["admissible_checks"])),
    ]
    return ResearchSummary(
        computed_at=computed_at,
        window_start=ex["start"],
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
        review=review,
        exec_hourly=_exec_hourly(ex["series"]),
        screened_hist=_screened_hist(sc["values"]),
        ladder_hourly=ResearchLadderSeries(ts=[h["hour"] for h in hourly], checks=[h["checks"] for h in hourly],
                                           violations=[h["violations"] for h in hourly]),
        method=copy.METHOD,
        note_path=NOTE_PATH,
    )
