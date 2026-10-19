"""Snapshot alignment and opportunity episodes (pure)."""
from dataclasses import dataclass
from datetime import datetime, timedelta
from statistics import median

from venue_arb.edges import Edge

ALIGN_S = 30
MAX_GAP_S = 150  # a missing tick at the 60 s cadence breaks an episode
MIN_EDGE = 0.01


def align(k_ts: list[datetime], pm_ts: list[datetime], tol_s: int = ALIGN_S) -> list[tuple[datetime, datetime]]:
    """Nearest Polymarket time within tol_s of each Kalshi time; each Polymarket time used once.
    Greedy in Kalshi-time order; ties go to the earlier Polymarket time."""
    free = sorted(pm_ts)
    out = []
    for k in sorted(k_ts):
        cand = [p for p in free if abs((p - k).total_seconds()) <= tol_s]
        if cand:
            p = min(cand, key=lambda p: (abs((p - k).total_seconds()), p))
            free.remove(p)
            out.append((k, p))
    return out


@dataclass(frozen=True)
class Episode:
    kalshi_id: str
    pm_id: str
    direction: str
    start: datetime
    end: datetime
    snapshots: int
    max_edge: float
    median_capacity_usd: float
    annualized: float


def find(points: list[tuple[datetime, Edge]], close_time: datetime, *, kalshi_id: str, pm_id: str,
         direction: str, min_capacity_usd: float = 100.0, min_snapshots: int = 2,
         max_gap_s: int = MAX_GAP_S) -> list[Episode]:
    runs: list[list[tuple[datetime, Edge]]] = []
    cur: list[tuple[datetime, Edge]] = []
    for t, e in sorted(points, key=lambda p: p[0]):
        if not (e.avg_edge is not None and e.avg_edge >= MIN_EDGE and e.capacity_usd >= min_capacity_usd):
            cur = []  # a non-qualifying tick ends the run
        elif cur and (t - cur[-1][0]).total_seconds() <= max_gap_s:
            cur.append((t, e))
        else:
            cur = [(t, e)]
            runs.append(cur)
    out = []
    for r in runs:
        if len(r) < min_snapshots:
            continue
        edges = [e for _, e in r]
        cost = median(e.capacity_usd / e.capacity_contracts for e in edges)
        days = max((close_time - r[-1][0]) / timedelta(days=1), 1 / 24)
        out.append(Episode(kalshi_id, pm_id, direction, r[0][0], r[-1][0], len(r),
                           max(e.best_edge for e in edges), median(e.capacity_usd for e in edges),
                           median(e.avg_edge for e in edges) / cost * 365 / days))
    return out
