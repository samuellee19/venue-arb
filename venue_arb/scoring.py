"""Book rows -> aligned per-snapshot edges -> edge and opportunity rows (pure)."""
from collections import defaultdict
from datetime import UTC, datetime

from venue_arb.edges import Edge, score
from venue_arb.episodes import Episode, align
from venue_arb.exact import ExactPair
from venue_arb.fees import US_EXECUTABLE, transfer_cost
from venue_arb.models import Level

Snaps = dict[datetime, list[Level]]


def snapshots(rows) -> dict[tuple[str, str], Snaps]:
    """(ts, venue, market_id, side, level, price, size) ask rows -> {(venue, market_id): {ts: levels}}, times
    ascending (naive times are UTC), levels sorted by side then level, so each side's ladder is best-first."""
    out: dict[tuple[str, str], dict[datetime, list[Level]]] = defaultdict(lambda: defaultdict(list))
    for ts, venue, market_id, side, level, price, size in rows:
        ts = ts if ts.tzinfo else ts.replace(tzinfo=UTC)
        out[(venue, market_id)][ts].append(Level(side, level, price, size))
    return {k: {t: sorted(v[t], key=lambda lv: (lv.side, lv.level)) for t in sorted(v)} for k, v in out.items()}


def pair_points(pair: ExactPair, k: Snaps, pm: Snaps, series_fee: dict) -> dict[str, list[tuple[datetime, Edge]]]:
    """Edges per direction at each aligned snapshot, stamped with the Kalshi snapshot time."""
    out: dict[str, list[tuple[datetime, Edge]]] = {"k_yes_pm_no": [], "k_no_pm_yes": []}
    for kt, pt in align(list(k), list(pm)):
        for e in score(pair, k[kt], pm[pt], series_fee):
            out[e.direction].append((kt, e))
    return out


def edge_row(pair: ExactPair, ts: datetime, e: Edge, fee_version: str) -> dict:
    return {"ts": ts, "kalshi_id": pair.kalshi_id, "pm_id": pair.pm_id, "direction": e.direction,
            "best_edge": e.best_edge, "capacity_contracts": e.capacity_contracts, "capacity_usd": e.capacity_usd,
            "avg_edge": e.avg_edge, "fee_version": fee_version}


def opportunity_row(ep: Episode) -> dict:
    return {"kalshi_id": ep.kalshi_id, "pm_id": ep.pm_id, "direction": ep.direction, "start_ts": ep.start,
            "end_ts": ep.end, "snapshots": ep.snapshots, "max_edge": ep.max_edge,
            "median_capacity_usd": ep.median_capacity_usd, "annualized": ep.annualized,
            "us_executable": US_EXECUTABLE["kalshi"] and US_EXECUTABLE["polymarket"],
            "transfer_cost_usd": transfer_cost(ep.median_capacity_usd)}
